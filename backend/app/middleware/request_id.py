"""
请求 ID 注入中间件

责任：
  1. 从请求头 X-Request-ID 读取或生成新的请求 ID
  2. 将 request_id 注入 contextvars，使整个调用链可追溯
  3. 将 request_id 写入响应头，便于前端排查问题

注意：使用纯 ASGI 中间件而非 BaseHTTPMiddleware，
      因为 BaseHTTPMiddleware 不兼容 WebSocket 连接（会返回 403）。
"""
from uuid import uuid4
from starlette.types import ASGIApp, Scope, Receive, Send
from app.middleware.request_context import set_request_id


class RequestIDMiddleware:
    """
    纯 ASGI 中间件：为每个请求（HTTP + WebSocket）注入唯一 request_id

    兼容 WebSocket：不依赖 BaseHTTPMiddleware 的 dispatch 模式，
    直接实现 ASGI 接口，对 HTTP 和 WebSocket 连接都能正确处理。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 只对 HTTP 请求设置 request_id（WebSocket 的 headers 在 scope 中路径不同）
        request_id: str = ""
        if scope["type"] == "http":
            # 从 HTTP 请求头获取 X-Request-ID
            for key, value in scope.get("headers", []):
                if key.lower() == b"x-request-id":
                    request_id = value.decode("utf-8", errors="replace").strip()
                    break

        if not request_id:
            request_id = uuid4().hex[:16]

        set_request_id(request_id)

        # 包装 send，对 HTTP 响应自动添加 X-Request-ID 头
        if scope["type"] == "http":
            async def wrapped_send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append(
                        (b"x-request-id", request_id.encode("utf-8"))
                    )
                    message["headers"] = headers
                await send(message)
        else:
            wrapped_send = send

        await self.app(scope, receive, wrapped_send)
