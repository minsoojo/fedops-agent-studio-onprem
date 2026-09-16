"""Small NDJSON bridge from blocking local inference to browser token streams."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

ChatRunner = Callable[[Callable[[str], None]], dict[str, Any]]


class _StreamCancelled(RuntimeError):
    """Stop blocking inference when the browser closes its response stream."""


def agent_chat_stream(runner: ChatRunner) -> StreamingResponse:
    """Run blocking inference off the response iterator and emit ordered events."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    cancelled = threading.Event()

    def emit_delta(content: str) -> None:
        if cancelled.is_set():
            raise _StreamCancelled("Agent response generation was stopped.")
        events.put({"type": "delta", "content": content})

    def run() -> None:
        try:
            events.put({"type": "stage", "stage": "generating"})
            result = runner(emit_delta)
            if not cancelled.is_set():
                events.put({"type": "completed", "result": result})
        except _StreamCancelled:
            pass
        except Exception as error:  # The response has started; report failure in-band.
            if not cancelled.is_set():
                events.put({"type": "error", "detail": str(error)})
        finally:
            events.put(None)

    async def generate() -> AsyncIterator[str]:
        worker = threading.Thread(target=run, name="agent-chat-stream", daemon=True)
        worker.start()
        try:
            while True:
                event = await asyncio.to_thread(events.get)
                if event is None:
                    return
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        finally:
            cancelled.set()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
