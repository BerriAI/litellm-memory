import asyncio
import logging

from .agent import answer

log = logging.getLogger(__name__)


async def worker(settings, store, slack, agent_fn=answer):
    while True:
        job = await store.next_job()
        if not job:
            await asyncio.sleep(0.4)
            continue
        try:
            payload = job["payload"]
            if job["kind"] == "leave":
                if not await store.channel_allowed(payload["team"], payload["channel"]):
                    try:
                        await slack.api("conversations.leave", {"channel": payload["channel"]})
                    except RuntimeError as exc:
                        if not any(code in str(exc) for code in ["not_in_channel", "cant_leave_general"]):
                            raise
                await store.result(job["id"])
                continue
            is_slack = job["kind"] == "slack"
            if is_slack:
                # Give an invitation event that arrived after this mention time to be processed.
                for _ in range(10):
                    if await slack.permitted(payload):
                        break
                    await asyncio.sleep(0.5)
                else:
                    await store.result(job["id"], status="ignored")
                    continue
                session = f"{payload['team']}:{payload['channel']}:{payload['thread_ts']}"
                history = await store.history(session)
            else:
                history = payload.get("history", [])
            result = job["answer"]
            if result is None:
                result = await asyncio.wait_for(
                    agent_fn(settings, payload["question"], history), timeout=settings.agent_timeout_seconds
                )
                await store.result(job["id"], answer=result, status="running")
            if is_slack:
                await slack.post_answer(job, result)
                await store.remember(session, payload["question"], result)
            await store.result(job["id"], answer=result)
        except asyncio.CancelledError:
            await store.result(job["id"], status="queued")
            raise
        except Exception as exc:
            # Exception messages can include upstream response bodies. Keep operational logs minimal.
            log.warning("Job %s failed (%s)", job["id"], type(exc).__name__)
            if job["kind"] != "web" and job["attempts"] < 3:
                await asyncio.sleep(min(job["attempts"] * 2, 6))
                await store.result(job["id"], status="queued")
            else:
                error = "I couldn’t complete that request. Please try again in a moment."
                await store.result(job["id"], status="failed", error=error)
                if job["kind"] == "slack":
                    try:
                        await slack.post_answer(job, error)
                    except Exception:
                        pass
