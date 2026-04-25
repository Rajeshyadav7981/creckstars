"""Tracked fire-and-forget tasks.

asyncio.create_task() alone drops the task reference — the task can be
garbage-collected mid-run, and shutdown won't wait for it. This module keeps
a strong reference for the task's lifetime and exposes a drain() the app
lifespan awaits so in-flight work finishes on restart.
"""
import asyncio
from typing import Set

from src.utils.logger import get_logger

logger = get_logger(__name__)

_tasks: Set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "Background task failed",
            extra={"extra_data": {"task": task.get_name(), "exc_type": type(exc).__name__, "error": str(exc)}},
        )


def fire_and_forget(coro, *, name: str | None = None) -> asyncio.Task:
    """Schedule a coroutine without awaiting it; the task is tracked for drain()."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


async def drain(timeout: float = 10.0) -> None:
    """Wait for outstanding tracked tasks; cancel any still running after timeout."""
    pending = {t for t in _tasks if not t.done()}
    if not pending:
        return
    logger.info(f"Draining {len(pending)} background task(s) (timeout={timeout}s)")
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for t in still_pending:
        t.cancel()
    if still_pending:
        # Give cancellations a brief moment to propagate.
        await asyncio.wait(still_pending, timeout=2.0)
        logger.warning(f"Cancelled {len(still_pending)} background task(s) that exceeded drain timeout")
