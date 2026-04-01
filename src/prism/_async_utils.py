from __future__ import annotations

import anyio


def _run_sync(async_fn, *args, **kwargs):
    """Run an async function from synchronous context.

    Creates an asyncio event loop via ``anyio.run`` and executes the
    coroutine.  Single-exception ``BaseExceptionGroup`` instances raised
    by ``anyio.create_task_group`` are unwrapped so callers see the
    original exception type.
    """

    async def _wrapper():
        return await async_fn(*args, **kwargs)

    try:
        return anyio.run(_wrapper, backend="asyncio")
    except BaseExceptionGroup as eg:
        if len(eg.exceptions) == 1:
            raise eg.exceptions[0] from eg
        raise
