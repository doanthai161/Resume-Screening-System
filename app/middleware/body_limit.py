"""Bound request bodies before multipart parsing, including chunked uploads."""

import asyncio

from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(
        self, app, max_bytes: int, timeout: float = 30, api_prefix: str = "/api/v1"
    ):
        self.app = app
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.api_prefix = api_prefix.rstrip("/")
        self.capacity = {"upload": 16, "auth": 64, "json": 32}
        self.active = dict.fromkeys(self.capacity, 0)
        self.clients = {}

    def _category(self, scope):
        path = scope.get("path", "").rstrip("/")
        if path == self.api_prefix + "/resumes":
            return "upload"
        if path.startswith(self.api_prefix + "/register/") or path.startswith(
            self.api_prefix + "/users/password/"
        ):
            return "auth"
        return "json"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        category = self._category(scope)
        limit = min(
            self.max_bytes,
            {"upload": self.max_bytes, "auth": 16 * 1024, "json": 1024 * 1024}[
                category
            ],
        )
        # scope.client is the server's resolved peer. Never trust an arbitrary
        # X-Forwarded-For header here; configure trusted proxies in the server.
        peer = (scope.get("client") or ("unknown", 0))[0]
        client_key = (category, peer)
        lengths = [v for k, v in scope.get("headers", []) if k == b"content-length"]
        if lengths:
            try:
                length = int(lengths[0])
                if len(lengths) != 1 or length < 0:
                    raise ValueError
            except ValueError:
                return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(
                    scope, receive, send
                )
            if length > limit:
                return await JSONResponse({"detail": "Request body too large"}, 413)(
                    scope, receive, send
                )
        # No await between admission check and increment: atomic on the ASGI
        # event loop. Reject excess clients before reading/buffering their body.
        if self.clients.get(client_key, 0) >= 2:
            return await JSONResponse(
                {"detail": "Too many concurrent request bodies"}, 429
            )(scope, receive, send)
        if self.active[category] >= self.capacity[category]:
            return await JSONResponse({"detail": "Request capacity exceeded"}, 503)(
                scope, receive, send
            )
        self.active[category] += 1
        self.clients[client_key] = self.clients.get(client_key, 0) + 1
        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                self.active[category] -= 1
                remaining = self.clients[client_key] - 1
                if remaining:
                    self.clients[client_key] = remaining
                else:
                    del self.clients[client_key]

        try:
            body = bytearray()

            async def read_body():
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return False
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        return 413
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        return True

            try:
                result = await asyncio.wait_for(
                    read_body(),
                    timeout=(
                        min(self.timeout, 5) if category == "auth" else self.timeout
                    ),
                )
            except asyncio.TimeoutError:
                return await JSONResponse({"detail": "Request body timed out"}, 408)(
                    scope, receive, send
                )
            if result is False:
                return
            if result == 413:
                return await JSONResponse({"detail": "Request body too large"}, 413)(
                    scope, receive, send
                )
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    data = bytes(body)
                    body.clear()
                    release()
                    return {"type": "http.request", "body": data, "more_body": False}
                return await receive()

            await self.app(scope, replay, send)
        finally:
            release()
