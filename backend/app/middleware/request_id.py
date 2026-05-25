"""
请求 ID 注入中间件

责任：
  1. 从请求头 X-Request-ID 读取或生成新的请求 ID
  2. 将 request_id 注入 contextvars，使整个调用链可追溯
  3. 将 request_id 写入响应头，便于前端排查问题
"""
from uuid import uuid4
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.middleware.request_context import set_request_id


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    ASGI 中间件：为每个 HTTP 请求注入唯一 request_id

    中间件执行顺序（洋葱模型）：
        请求进入 → dispatch() 前半段 → 下一个中间件/路由 → dispatch() 后半段 → 响应返回

    BaseHTTPMiddleware 的底层机制：
        继承后只需重写 dispatch() 方法。
        BaseHTTPMiddleware.__call__() 中会自动处理 await self.app(scope, receive, send)，
        你不需要手动调用 next() 或 self.app()。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        中间件入口：先从请求头读取 request_id，若不存在则生成新的
        call_next 是 Starlette 传入的可调用对象，调用它会继续执行后续中间件链
        """
        # 1. 尝试从请求头获取（支持前端或上游网关透传）
        request_id: str = request.headers.get("X-Request-ID", "").strip()

        # 2. 如果客户端没有传，自动生成一个
        if not request_id:
            request_id = uuid4().hex[:16]  # 截断 16 位，足够唯一且不冗余

        # 3. 注入 contextvars，使后续所有代码（包括后台任务）都能通过 get_request_id() 获取
        set_request_id(request_id)

        # 4. 同时挂载到 request.state，方便直接通过 request.state.request_id 访问
        request.state.request_id = request_id

        # 5. 执行后续中间件链和路由处理
        try:
            response: Response = await call_next(request)
        except Exception:
            # 即使下游抛出异常，也要在响应头中带上 request_id（异常处理器会用到）
            # 这里不吞掉异常，重新抛出让全局 exception_handler 处理
            raise

        # 6. 将 request_id 写入响应头，前端可以从响应中提取用于排查
        response.headers["X-Request-ID"] = request_id
        return response
