import asyncio
import logging

from .agent import answer
from .progress import reporter

log = logging.getLogger(__name__)


async def worker(settings, store, slack, agent_fn=answer, running=None):
    running = running if running is not None else {}
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
                history = await slack.history(job)
            else:
                history = payload.get("history", [])
            result = job["answer"]
            if result is None:
                async def progress(phase, partial):
                    await store.progress(job["id"], phase, partial)
                context_token = reporter.set(progress)
                try:
                    task = asyncio.create_task(agent_fn(settings, payload["question"], history))
                    running[job["id"]] = task
                    current = await store.get_job(job["id"])
                    if current["status"] == "cancelled":
                        task.cancel()
                    result = await asyncio.wait_for(task, timeout=settings.agent_timeout_seconds)
                finally:
                    running.pop(job["id"], None)
                    reporter.reset(context_token)
                await store.result(job["id"], answer=result, status="running")
            if is_slack:
                if await slack.post_answer(job, result):
                    await store.remember(session, payload["question"], result)
            await store.result(job["id"], answer=result)
        except asyncio.CancelledError:
            current = await store.get_job(job["id"])
            if current and current["status"] == "cancelled" and not asyncio.current_task().cancelling():
                continue
            await store.result(job["id"], status="queued")
            raise
        except Exception as exc:
            # Exception messages can include upstream response bodies. Keep operational logs minimal.
            log.warning("Job %s failed (%s)", job["id"], type(exc).__name__)
            if job["kind"] != "web" and job["attempts"] < 3:
                await asyncio.sleep(min(job["attempts"] * 2, 6))
                await store.result(job["id"], status="queued")
            else:
                error = ("This answer took too long. Try a more specific question or ask about one topic at a time."
                         if isinstance(exc, TimeoutError) else
                         "Moegent couldn’t reach the model or memory service. Your question is saved here—try again in a moment.")
                await store.result(job["id"], status="failed", error=error)
                if job["kind"] == "slack":
                    try:
                        await slack.post_answer(job, error)
                    except Exception:
                        pass
