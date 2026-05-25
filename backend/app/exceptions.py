"""
统一异常体系

设计动机：
  前端需要结构化的错误响应 {code, message, detail}，而非裸 HTTP 500。
  FastAPI 的全局 exception_handler 机制允许我们在任何层抛出 AppException，
  由中间层统一捕获并格式化为一致响应，避免每个路由都写 try-except。

使用方式：
  raise AppException(status_code=404, error_code="USER_NOT_FOUND", detail="用户不存在")
"""
from typing import Any
from enum import Enum
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


# ==================== 错误码枚举 ====================
class ErrorCode(str, Enum):
    """
    业务错误码，新增错误类型只需在此添加枚举值
    格式：类别_子类别，便于前端按类别处理不同逻辑
    """
    # 通用 1xxx
    INTERNAL_ERROR = "INTERNAL_ERROR"           # 未知内部错误
    VALIDATION_ERROR = "VALIDATION_ERROR"       # 请求参数校验失败
    NOT_FOUND = "NOT_FOUND"                     # 资源不存在
    UNAUTHORIZED = "UNAUTHORIZED"               # 未认证
    FORBIDDEN = "FORBIDDEN"                     # 无权限
    RATE_LIMITED = "RATE_LIMITED"               # 频率限制

    # Agent 相关 2xxx
    AGENT_SESSION_NOT_FOUND = "AGENT_SESSION_NOT_FOUND"
    AGENT_STEP_LIMIT_EXCEEDED = "AGENT_STEP_LIMIT_EXCEEDED"
    AGENT_TASK_FAILED = "AGENT_TASK_FAILED"

    # 沙箱相关 3xxx
    SANDBOX_NOT_AVAILABLE = "SANDBOX_NOT_AVAILABLE"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    SANDBOX_BLOCKED_URL = "SANDBOX_BLOCKED_URL"
    SANDBOX_HIGH_RISK_ACTION = "SANDBOX_HIGH_RISK_ACTION"

    # 审批相关 4xxx
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"

    # 数据库/缓存 5xxx
    DB_CONNECTION_ERROR = "DB_CONNECTION_ERROR"
    REDIS_CONNECTION_ERROR = "REDIS_CONNECTION_ERROR"


# ==================== 自定义异常类 ====================
class AppException(Exception):
    """
    项目统一异常基类
    任何模块想向上层抛出可被前端解析的错误时，raise AppException 即可
    """
    def __init__(
        self,
        status_code: int = 500,
        error_code: ErrorCode | str = ErrorCode.INTERNAL_ERROR,
        message: str = "服务器内部错误",
        detail: Any = None,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code.value if isinstance(error_code, ErrorCode) else error_code
        self.message = message
        self.detail = detail
        super().__init__(message)


# ==================== 全局异常处理器注册 ====================
def register_exception_handlers(app) -> None:
    """
    向 FastAPI app 注册所有异常处理器
    调用方式：在 create_app() 工厂函数中调用 register_exception_handlers(app)
    """

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """捕获项目自定义异常，按结构化格式返回"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.error_code,
                "message": exc.message,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """捕获 Starlette 原生 HTTP 异常，统一格式返回"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": exc.detail,
                "detail": None,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """捕获 FastAPI 请求参数校验失败，提取字段级错误信息返回"""
        # 提取 Pydantic 校验错误的具体字段和原因
        field_errors: list[dict[str, Any]] = []
        for error in exc.errors():
            field_errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "请求参数校验失败",
                "detail": field_errors,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底处理器：捕获所有未被以上处理器捕获的异常，防止生产环境裸奔"""
        # TODO: 后续接入 structlog 记录完整 traceback
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "服务器内部未知错误",
                "detail": str(exc) if app.debug else None,
            },
        )
