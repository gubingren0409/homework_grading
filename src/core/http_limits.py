import asyncio
import json
from typing import Callable, Awaitable, Dict, Any

from starlette.types import ASGIApp, Scope, Receive, Send, Message


class RequestBodyReadTimeout(RuntimeError):
    pass


class HardBodyLimitMiddleware:
    """
    ASGI-level hard body limiter with slowloris timeout protection.
    - Reject by Content-Length if declared size already exceeds cap.
    - Enforce runtime byte counter for chunked/unknown-size uploads.
    - Timeout each receive() call to prevent slowloris socket occupation.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        read_timeout_seconds: float,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.read_timeout_seconds = read_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (scope.get("headers") or [])
        }
        content_length_raw = headers.get("content-length")
        if content_length_raw:
            try:
                content_length = int(content_length_raw)
            except ValueError:
                content_length = -1
            if content_length > self.max_body_bytes:
                await self._send_error(send, 413, "payload too large")
                return

        state: Dict[str, Any] = {
            "seen": 0,
            "aborted": False,
            "response_started": False,
        }

        async def wrapped_send(message: Message) -> None:
            if state["aborted"]:
                return
            if message.get("type") == "http.response.start":
                state["response_started"] = True
            await send(message)

        async def wrapped_receive() -> Message:
            if state["aborted"]:
                return {"type": "http.disconnect"}

            try:
                msg = await asyncio.wait_for(receive(), timeout=self.read_timeout_seconds)
            except asyncio.TimeoutError as exc:
                state["aborted"] = True
                if not state["response_started"]:
                    await self._send_error(send, 408, "request body read timeout")
                raise RequestBodyReadTimeout("request body read timeout") from exc

            if msg.get("type") == "http.request":
                body = msg.get("body", b"")
                state["seen"] += len(body)
                if state["seen"] > self.max_body_bytes:
                    state["aborted"] = True
                    if not state["response_started"]:
                        await self._send_error(send, 413, "payload too large")
                    return {"type": "http.disconnect"}
            return msg

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except RequestBodyReadTimeout:
            return

    @staticmethod
    async def _send_error(send: Send, status_code: int, detail: str) -> None:
        payload = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": payload,
                "more_body": False,
            }
        )
