from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def _gather(*callables):
    """Run zero-arg callables concurrently in threads, return results in order."""
    with ThreadPoolExecutor(max_workers=len(callables)) as pool:
        return list(pool.map(lambda fn: fn(), callables))
