import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

from .access import AccessPolicy, Principal

log = logging.getLogger(__name__)
TERMINAL = {"done", "failed", "cancelled", "ignored"}


class SlackAPIError(RuntimeError):
    def __init__(self, code, retry_after=0):
        self.code = code if re.fullmatch(r"[a-z_]{1,80}", str(code)) else "unknown_error"
        self.retry_after = retry_after
        super().__init__("Slack API: " + self.code)


def slack_text(text):
    # Accessible notification fallback. Code spans and fenced examples stay literal.
    pieces = re.split(r"(```[\s\S]*?```|`[^`\n]+`)", text)
    for index in range(0, len(pieces), 2):
        value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"<\2|\1>", pieces[index])
        value = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", value)
        pieces[index] = re.sub(r"(?m)^#{1,6} (.+)$", r"*\1*", value)
    return "".join(pieces)


def formatted(text):
    # Slack's native AI Markdown block renders headings, lists, code, links and tables.
    return {"text": slack_text(text), "blocks": [{"type": "markdown", "text": text}]}


class Slack:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.running = {}
        self.event_lock = asyncio.Lock()
        self.delivery_locks = defaultdict(asyncio.Lock)
        self.last_update = {}
        self.last_display = {}
        self.conversation_cache = {}
        self.conversation_locks = defaultdict(asyncio.Lock)
        self.reaction_retry = {}

    async def token(self):
        return await self.store.setting("slack_bot_token") or self.settings.slack_bot_token

    async def bot_id(self):
        return await self.store.setting("slack_bot_user_id") or self.settings.slack_bot_user_id

    async def api(self, method, payload):
        token = await self.token()
        if not token:
            raise RuntimeError("Slack is not installed yet")
        async with httpx.AsyncClient(timeout=20) as client:
            kwargs = {"headers": {"Authorization": f"Bearer {token}"}}
            if method in {"conversations.replies", "conversations.info", "conversations.members", "users.info"}:
                response = await client.get(f"https://slack.com/api/{method}", params=payload, **kwargs)
            else:
                response = await client.post(f"https://slack.com/api/{method}", json=payload, **kwargs)
        if response.status_code == 429:
            try:
                retry_after = min(3600, max(1, float(response.headers.get("retry-after", "30"))))
            except ValueError:
                retry_after = 30
            raise SlackAPIError("ratelimited", retry_after)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackAPIError(data.get("error", "unknown_error"))
        return data

    async def message_api(self, method, payload):
        try:
            return await self.api(method, payload)
        except RuntimeError as error:
            if "invalid_blocks" not in str(error):
                raise
            # A workspace without the AI Markdown block can still receive the answer.
            return await self.api(method, {**payload, "blocks": []})

    async def optional_api(self, method, payload):
        try:
            return await self.message_api(method, payload) if method in {"chat.postMessage", "chat.update"} else await self.api(method, payload)
        except Exception:
            # Visual feedback must never prevent delivery of an answer.
            return None

    async def permitted(self, payload):
        principal = Principal("slack", payload["user"], payload["team"])
        try:
            AccessPolicy(self.settings).authorize(principal)
        except HTTPException:
            return False
        if payload["channel"].startswith("D") or payload.get("channel_type") == "im":
            return True
        approved = await self.store.channel_approval(payload["team"], payload["channel"])
        if approved is not None:
            return approved
        # Group DMs can have C-prefixed IDs and do not reliably emit a bot invitation event.
        # Slack's actual conversation metadata, never the ID prefix, proves this is an MPIM
        # created by Moe. Explicit removals/denials above must never be overridden.
        info = await self.conversation_info(payload["channel"])
        if (info.get("is_mpim") and info.get("is_member")
                and info.get("creator") == self.settings.slack_owner_user_id
                and info.get("created")):
            await self.store.channel_event(payload["team"], payload["channel"], True, info["created"])
            return await self.store.channel_allowed(payload["team"], payload["channel"])
        return False

    async def conversation_info(self, channel):
        async with self.conversation_locks[channel]:
            cached = self.conversation_cache.get(channel)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            try:
                info = (await self.api("conversations.info", {"channel": channel}))["channel"]
            except Exception as exc:
                log.warning("Slack conversation lookup failed (%s)",
                            exc.code if isinstance(exc, SlackAPIError) else type(exc).__name__)
                info = {}
            self.conversation_cache[channel] = (time.monotonic() + 30, info)
            return info

    async def reaction(self, job_id, payload, active):
        key = (job_id, active)
        deadline, attempts = self.reaction_retry.get(key, (0, 0))
        if time.monotonic() < deadline:
            return False
        method = "reactions.add" if active else "reactions.remove"
        try:
            await self.api(method, payload)
        except Exception as exc:
            code = exc.code if isinstance(exc, SlackAPIError) else type(exc).__name__
            completed = {"already_reacted"} if active else {"no_reaction", "message_not_found", "channel_not_found"}
            if code not in completed:
                delay = max(min(30, 2 ** min(attempts + 1, 5)), getattr(exc, "retry_after", 0))
                self.reaction_retry[key] = (time.monotonic() + delay, attempts + 1)
                log.warning("Slack %s failed (%s); retrying in %.0fs", method, code, delay)
                return False
        self.reaction_retry.pop(key, None)
        return True

    async def event(self, envelope):
        # Serialize duplicate deliveries and steering changes, with only local DB work before ACK.
        async with self.event_lock:
            await self._event(envelope)

    async def _event(self, envelope):
        event = envelope.get("event", {})
        team = envelope.get("team_id", "")
        if team != self.settings.slack_team_id:
            return
        if self.settings.slack_app_id and envelope.get("api_app_id") != self.settings.slack_app_id:
            return
        bot_id = await self.bot_id()
        if not bot_id:
            return
        kind, channel = event.get("type"), event.get("channel", "")
        timestamp = event.get("event_ts", envelope.get("event_time", 0))
        if kind in {"member_joined_channel", "member_left_channel"} and event.get("user") == bot_id:
            owner_invited = kind == "member_joined_channel" and AccessPolicy(self.settings).owner(
                Principal("slack", event.get("inviter", ""), team)
            )
            await self.store.channel_event(team, channel, owner_invited, timestamp)
            if not owner_invited and kind == "member_joined_channel":
                await self.store.enqueue("slack", "leave", {"team": team, "channel": channel},
                                         "leave:" + envelope["event_id"])
            return
        if kind not in {"message", "app_mention"} or not channel or not event.get("ts"):
            return
        if event.get("bot_id") or event.get("subtype") or not event.get("user") or event["user"] == bot_id:
            return
        principal = Principal("slack", event["user"], team)
        try:
            AccessPolicy(self.settings).authorize(principal)
        except HTTPException:
            return
        thread = event.get("thread_ts", event["ts"])
        jobs = await self.store.slack_thread_jobs(team, channel, thread)
        mentioned = f"<@{bot_id}>" in event.get("text", "")
        direct = event.get("channel_type") == "im" or channel.startswith("D")
        # Subscribe to channel messages for follow-ups, without replying to unrelated conversations.
        if kind != "app_mention" and not direct and not mentioned and not (event.get("thread_ts") and jobs):
            return
        text = re.sub(r"<@" + re.escape(bot_id) + r">", "", event.get("text", "")).strip()
        if not text:
            return
        dedupe = f"slack:{team}:{channel}:{event['ts']}"
        if await self.store.by_dedupe(dedupe):
            return
        active = [job for job in jobs if job["status"] in {"queued", "running"}
                  and job["payload"]["user"] == event["user"]
                  and float(job["payload"].get("message_ts", job["payload"]["thread_ts"])) < float(event["ts"])]
        prior = []
        for job in active:
            prior.extend(job["payload"].get("prior_questions", []))
            prior.append(job["payload"]["question"])
            await self.store.cancel(job["id"])
            if task := self.running.get(job["id"]):
                task.cancel()
        payload = {"team": team, "channel": channel, "user": event["user"], "thread_ts": thread,
                   "channel_type": event.get("channel_type"), "message_ts": event["ts"],
                   "question": text[:16000], "prior_questions": prior[-8:]}
        payload = await self.prepare_payload(payload)
        job_id = await self.store.enqueue(principal.identity, "slack", payload, dedupe)
        if text.lower().strip(".!") in {"stop", "cancel", "stop please", "please stop"}:
            await self.store.cancel(job_id)

    async def prepare_payload(self, payload):
        return payload

    async def history(self, job):
        p = job["payload"]
        bot_id = await self.bot_id()
        jobs = await self.store.slack_thread_jobs(p["team"], p["channel"], p["thread_ts"])
        # Ignore placeholder/status replies; completed answers are useful conversation context.
        placeholders = {j["response_ts"] for j in jobs if j["status"] != "done" and j["response_ts"]}
        messages, cursor, truncated = [], None, False
        try:
            for _ in range(10):
                args = {"channel": p["channel"], "ts": p["thread_ts"], "limit": 100,
                        "latest": p.get("message_ts", p["thread_ts"]), "inclusive": "false"}
                if cursor:
                    args["cursor"] = cursor
                data = await self.api("conversations.replies", args)
                messages.extend(data.get("messages", []))
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            truncated = bool(cursor) or len(messages) > 60
            # Include the opening request and the most recent context within a bounded prompt.
            messages = [messages[0], *messages[-59:]] if len(messages) > 60 else messages
            history = []
            for message in messages:
                ts = message.get("ts", "0")
                if ts in placeholders or float(ts) >= float(p.get("message_ts", p["thread_ts"])):
                    continue
                text = message.get("text", "").strip()
                if not text or message.get("subtype") in {"message_deleted", "channel_join", "channel_leave"}:
                    continue
                ours = message.get("user") == bot_id
                history.append({"role": "assistant" if ours else "user",
                                "content": text[:3000] if ours else f"<@{message.get('user', 'unknown')}>: {text[:3000]}"})
            if truncated:
                notice = ("[Only the first 1,000 thread messages were retrieved; later context may be missing.]" if cursor else
                          "[Thread context is shortened; some older messages are omitted.]")
                history.insert(0, {"role": "user", "content": notice})
            return history
        except Exception:
            # Missing new OAuth scopes should degrade gracefully until Slack is reauthorized.
            session = f"{p['team']}:{p['channel']}:{p['thread_ts']}"
            history = await self.store.history(session)
            history.extend({"role": "user", "content": q} for q in p.get("prior_questions", []))
            if p["thread_ts"] != p.get("message_ts"):
                history.insert(0, {"role": "user", "content": "[Slack thread retrieval is unavailable. Only LiteLLM Memory's saved exchanges follow; do not claim to have read the full thread.]"})
            return history

    async def monitor(self):
        while True:
            for job in await self.store.slack_indicators():
                try:
                    await self.indicator(job["id"])
                except Exception:
                    log.warning("Slack feedback unavailable for job %s", job["id"])
            await asyncio.sleep(0.8)

    async def indicator(self, job_id):
        async with self.delivery_locks[job_id]:
            record = await self.store.get_job(job_id)
            if not record or record["indicator_done"]:
                return
            p = json.loads(record["payload"])
            terminal = record["status"] in TERMINAL
            if not terminal and not await self.permitted(p):
                return
            reaction = {"channel": p["channel"], "timestamp": p.get("message_ts", p["thread_ts"]), "name": "eyes"}
            if terminal:
                # Clean up even after an add timeout: Slack may have accepted that request.
                if record["status"] != "ignored" and not await self.reaction(job_id, reaction, False):
                    return
                if record["indicator_active"] and p["channel"].startswith("D"):
                    await self.optional_api("assistant.threads.setStatus", {
                        "channel_id": p["channel"], "thread_ts": p["thread_ts"], "status": ""})
                if record["status"] == "cancelled" and record["response_ts"]:
                    await self.optional_api("chat.update", {"channel": p["channel"], "ts": record["response_ts"], "text": "Stopped."})
                await self.store.slack_delivery(job_id, active=0, done=1)
                self.reaction_retry.pop((job_id, True), None)
                self.last_update.pop(job_id, None)
                self.last_display.pop(job_id, None)
                return
            if not record["indicator_active"]:
                if await self.reaction(job_id, reaction, True):
                    await self.store.slack_delivery(job_id, active=1)
            now = time.monotonic()
            if now - self.last_update.get(job_id, 0) < 2.5:
                return
            status = record["phase"] if record["status"] == "running" else "Queued"
            display = (status, record["partial"])
            if self.last_display.get(job_id) == display and now - self.last_update.get(job_id, 0) < 50:
                return
            if p["channel"].startswith("D"):
                await self.optional_api("assistant.threads.setStatus", {
                    "channel_id": p["channel"], "thread_ts": p["thread_ts"], "status": status})
            status_text = status if status.endswith((".", "!", "?", "…")) else status + "…"
            text = record["partial"][:11000] + "\n\n_Working…_" if record["partial"] else status_text
            if record["response_ts"]:
                await self.optional_api("chat.update", {"channel": p["channel"], "ts": record["response_ts"], **formatted(text)})
            else:
                response = await self.optional_api("chat.postMessage", {
                    "channel": p["channel"], "thread_ts": p["thread_ts"], **formatted(text),
                    "unfurl_links": False, "unfurl_media": False,
                    "client_msg_id": str(uuid.uuid5(uuid.NAMESPACE_URL, job_id + ":status")),
                })
                if response and response.get("ts"):
                    await self.store.slack_delivery(job_id, response_ts=response["ts"])
            self.last_update[job_id] = now
            self.last_display[job_id] = display

    async def post_answer(self, job, text):
        async with self.delivery_locks[job["id"]]:
            record = await self.store.get_job(job["id"])
            payload = job["payload"]
            if record["status"] == "cancelled" or not await self.permitted(payload):
                return False
            ids = list(dict.fromkeys(re.findall(re.escape(self.settings.public_base_url) + r"/memory/([\w-]+)", text)))
            if len(ids) > 1:
                text += f"\n\n[View all {len(ids)} sources]({self.settings.public_base_url}/sources?{urlencode({'ids': ','.join(ids)})})"
            parts, current = [], ""
            for line in text.splitlines(keepends=True):
                while len(line) > 11000:
                    if current:
                        parts.append(current)
                        current = ""
                    parts.append(line[:11000])
                    line = line[11000:]
                if len(current) + len(line) > 11000:
                    parts.append(current)
                    current = ""
                current += line
            if current:
                parts.append(current)
            for index, part in enumerate(parts):
                if (await self.store.get_job(job["id"]))["status"] == "cancelled":
                    return False
                args = {"channel": payload["channel"], **formatted(part), "unfurl_links": False, "unfurl_media": False}
                if index == 0 and record["response_ts"]:
                    await self.message_api("chat.update", {**args, "ts": record["response_ts"]})
                else:
                    result = await self.message_api("chat.postMessage", {**args, "thread_ts": payload["thread_ts"],
                        "client_msg_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job['id']}:{index}"))})
                    if index == 0 and result.get("ts"):
                        await self.store.slack_delivery(job["id"], response_ts=result["ts"])
            # Mark terminal inside the delivery lock so the monitor cannot overwrite the final answer.
            await self.store.result(job["id"], answer=text, status="failed" if record["status"] == "failed" else "done", error=record["error"])
            return True

    async def close(self):
        with contextlib.suppress(Exception):
            for job in await self.store.slack_indicators():
                if job["indicator_active"]:
                    p = job["payload"]
                    await self.optional_api("reactions.remove", {"channel": p["channel"], "timestamp": p.get("message_ts", p["thread_ts"]), "name": "eyes"})
                    await self.store.slack_delivery(job["id"], active=0)
