import re
import uuid

import httpx
from fastapi import HTTPException

from .access import AccessPolicy, Principal


class Slack:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def token(self):
        return await self.store.setting("slack_bot_token") or self.settings.slack_bot_token

    async def bot_id(self):
        return await self.store.setting("slack_bot_user_id") or self.settings.slack_bot_user_id

    async def api(self, method, payload):
        token = await self.token()
        if not token:
            raise RuntimeError("Slack is not installed yet")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {token}"}, json=payload,
            )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            # Slack errors are identifiers; never include request headers or tokens.
            raise RuntimeError("Slack API: " + str(data.get("error", "unknown_error")))
        return data

    async def permitted(self, payload):
        principal = Principal("slack", payload["user"], payload["team"])
        try:
            AccessPolicy(self.settings).authorize(principal)
        except HTTPException:
            return False
        return payload["channel"].startswith("D") or await self.store.channel_allowed(
            payload["team"], payload["channel"]
        )

    async def event(self, envelope):
        """Persist events before acknowledging them. Never wait on a model here."""
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
        direct = kind == "message" and event.get("channel_type") == "im"
        if kind != "app_mention" and not direct:
            return
        if event.get("bot_id") or event.get("subtype") or not event.get("user"):
            return
        if event["user"] == bot_id:
            return
        principal = Principal("slack", event["user"], team)
        try:
            AccessPolicy(self.settings).authorize(principal)
        except HTTPException:
            return
        text = re.sub(r"<@" + re.escape(bot_id) + r">", "", event.get("text", "")).strip()
        if not text:
            return
        payload = {"team": team, "channel": channel, "user": event["user"],
                   "thread_ts": event.get("thread_ts", event.get("ts")), "question": text[:16000]}
        # Membership and mentions may arrive out of order. The worker checks the approval.
        await self.store.enqueue(principal.identity, "slack", payload, "event:" + envelope["event_id"])

    async def post_answer(self, job, text):
        payload = job["payload"]
        if not await self.permitted(payload):
            return
        # Slack mrkdwn links use <URL|label> rather than Markdown links.
        text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"<\2|\1>", text)
        # Split long answers at line boundaries. Stable IDs limit duplicate delivery on retries.
        parts, current = [], ""
        for line in text.splitlines(keepends=True):
            while len(line) > 3500:
                if current:
                    parts.append(current)
                    current = ""
                parts.append(line[:3500])
                line = line[3500:]
            if len(current) + len(line) > 3500:
                parts.append(current)
                current = ""
            current += line
        if current:
            parts.append(current)
        for index, part in enumerate(parts):
            await self.api("chat.postMessage", {
                "channel": payload["channel"], "thread_ts": payload["thread_ts"], "text": part,
                "unfurl_links": False, "unfurl_media": False,
                "client_msg_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job['id']}:{index}")),
            })
