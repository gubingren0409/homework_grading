import asyncio
import contextvars
import threading
from typing import Any, Dict


def run_async_in_sync_context(coro):
    """
    Run an async coroutine from sync worker code.

    Behavior:
    - If already inside a running loop, hop to a dedicated thread.
    - If not inside a running loop, create a loop for the current thread when needed.
    - Works on Windows/Python 3.12 threads where get_event_loop() no longer auto-creates.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            loop.close()

    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, BaseException] = {}
    parent_ctx = contextvars.copy_context()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder["result"] = parent_ctx.run(loop.run_until_complete, coro)
        except BaseException as exc:
            error_holder["error"] = exc
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("result")

