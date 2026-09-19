from contextvars import ContextVar

# Each queued request gets its own reporter; model internals and tool arguments never enter the UI.
reporter = ContextVar("progress_reporter", default=None)


async def report(phase, partial=None):
    callback = reporter.get()
    if callback:
        await callback(phase, partial)
