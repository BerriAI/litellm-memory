import asyncio
import logging

from .archive import Reader
from .errors import explain_error
from .progress import reporter
from .query import CitationError, answer_work

log = logging.getLogger(__name__)


async def query_worker(settings, store, slack, archive, gateway, running, agent_fn=answer_work):
    while True:
        job = await store.next_job()
        if not job:
            await asyncio.sleep(0.3)
            continue
        payload = job["payload"]
        if job["kind"] == "leave":
            try:
                if not await store.channel_allowed(payload["team"], payload["channel"]):
                    await slack.api("conversations.leave", {"channel": payload["channel"]})
                await store.result(job["id"])
            except Exception:
                await store.result(job["id"], status="failed", error="Channel access remains disabled.")
            continue
        is_slack = job["kind"] == "slack"
        try:
            reader = await slack.reader(payload) if is_slack else Reader(payload["principal"], payload.get("audience", "owner"))
            stamp = await archive.policy_stamp(reader)
            history = await slack.history(job) if is_slack else payload.get("history", [])
            # Do not reuse private replies if a conversation's delivery audience has changed.
            if reader.audience == "public":
                history = []

            async def progress(phase, partial):
                if stamp != await archive.policy_stamp(reader):
                    raise PermissionError("Access changed during this answer.")
                await store.progress(job["id"], phase, partial)

            context = reporter.set(progress)
            try:
                frozen_gateway = gateway.snapshot()
                async def run_answer():
                    async with frozen_gateway:
                        return await agent_fn(frozen_gateway.settings, archive, frozen_gateway, reader,
                                              payload["question"], history, model=payload.get("model"),
                                              scope=payload.get("scope"))
                task = asyncio.create_task(run_answer())
                running[job["id"]] = task
                current = await store.get_job(job["id"])
                if current["status"] == "cancelled":
                    task.cancel()
                result = await asyncio.wait_for(task, settings.agent_timeout_seconds)
            finally:
                running.pop(job["id"], None)
                reporter.reset(context)
            if stamp != await archive.policy_stamp(reader):
                raise PermissionError("Access changed during this answer.")
            if is_slack:
                if await slack.reader(payload) != reader:
                    raise PermissionError("This conversation's audience changed.")
                if await slack.post_answer(job, result):
                    session = f"{payload['team']}:{payload['channel']}:{payload['thread_ts']}"
                    await store.remember(session, payload["question"], result)
            await store.result(job["id"], answer=result)
        except asyncio.CancelledError:
            current = await store.get_job(job["id"])
            if current and current["status"] == "cancelled" and not asyncio.current_task().cancelling():
                continue
            await store.result(job["id"], status="queued")
            raise
        except Exception as exc:
            log.warning("Query failed (%s)", type(exc).__name__)
            error = ("Access changed or this conversation cannot use those memories. Open the dashboard to continue."
                     if isinstance(exc, PermissionError) else
                     "The answer's source links could not be verified. Please retry the question."
                     if isinstance(exc, CitationError) else explain_error(exc).message)
            await store.result(job["id"], status="failed", error=error)
            if is_slack:
                try:
                    await slack.post_answer(job, error)
                except Exception:
                    pass
