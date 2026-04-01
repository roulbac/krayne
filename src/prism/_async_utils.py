from __future__ import annotations

import functools

import anyio


def _run_async(async_fn):
    """Run a zero-arg async callable from synchronous context."""
    try:
        return anyio.run(async_fn, backend="asyncio")
    except BaseExceptionGroup as eg:
        if len(eg.exceptions) == 1:
            raise eg.exceptions[0] from eg
        raise


def _gather(*callables):
    """Run zero-arg callables concurrently in threads, return results in order."""
    results = [None] * len(callables)

    async def _run():
        async with anyio.create_task_group() as tg:
            for i, fn in enumerate(callables):

                async def _task(idx=i, func=fn):
                    results[idx] = await anyio.to_thread.run_sync(func)

                tg.start_soon(_task)

    _run_async(_run)
    return results
