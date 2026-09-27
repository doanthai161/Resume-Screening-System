"""Bound CPU/memory-heavy password work without blocking the event loop."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from weakref import WeakKeyDictionary

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="password")
_limits = WeakKeyDictionary()


async def run_password_work(function, *args):
    loop = asyncio.get_running_loop()
    limit = _limits.setdefault(loop, asyncio.Semaphore(2))
    await limit.acquire()
    try:
        future = loop.run_in_executor(_executor, function, *args)
    except BaseException:
        limit.release()
        raise

    def finished(result):
        limit.release()
        # Consume errors even if the request was cancelled while work continued.
        if not result.cancelled():
            result.exception()

    future.add_done_callback(finished)
    # Cancellation must not free capacity while the native hash is still running.
    return await asyncio.shield(future)
