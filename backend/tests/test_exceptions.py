"""
测试 exceptions.py 模块
运行方式：在 backend/ 目录下执行 pytest tests/test_exceptions.py -v
要求：fastapi + httpx 已安装在虚拟环境
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.exceptions import (
    AppException,
    ErrorCode,
    register_exception_handlers,
)


# ==================== 辅助：创建测试用 FastAPI 应用 ====================
def _make_test_app() -> FastAPI:
    """创建一个最小 FastAPI app 并注册异常处理器和测试路由"""
    app = FastAPI(debug=False)  # debug=False 让 ServerErrorMiddleware 返回纯文本而非 HTML 调试页
    register_exception_handlers(app)

    @app.get("/test/app-error")
    async def trigger_app_error() -> None:
        raise AppException(
            status_code=403,
            error_code=ErrorCode.FORBIDDEN,
            message="无权访问此资源",
            detail={"required_role": "admin"},
        )

    @app.get("/test/validation-error")
    async def trigger_validation_error(q: int = Query(ge=10)) -> dict:
        return {"value": q}

    @app.get("/test/unhandled-error")
    async def trigger_unhandled_error() -> None:
        raise ValueError("模拟未捕获异常")

    return app


@pytest.fixture
def client() -> TestClient:
    """FastAPI 同步测试客户端
    raise_server_exceptions=False：让 ServerErrorMiddleware 放行未处理异常到我们的 handler
    """
    return TestClient(_make_test_app(), raise_server_exceptions=False)


# ==================== 单元测试：ErrorCode 枚举 ====================
class TestErrorCode:
    """验证错误码枚举完整性"""

    def test_error_code_is_string_enum(self) -> None:
        """所有错误码值必须是字符串"""
        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert len(code.value) > 0

    def test_agent_codes_exist(self) -> None:
        """Agent 相关错误码必须存在"""
        assert ErrorCode.AGENT_SESSION_NOT_FOUND.value == "AGENT_SESSION_NOT_FOUND"
        assert ErrorCode.AGENT_STEP_LIMIT_EXCEEDED.value == "AGENT_STEP_LIMIT_EXCEEDED"

    def test_sandbox_codes_exist(self) -> None:
        """沙箱相关错误码必须存在"""
        assert ErrorCode.SANDBOX_BLOCKED_URL.value == "SANDBOX_BLOCKED_URL"


# ==================== 单元测试：AppException 类 ====================
class TestAppException:
    """验证自定义异常类构造"""

    def test_basic_exception_creation(self) -> None:
        exc = AppException(status_code=404, error_code=ErrorCode.NOT_FOUND, message="资源不存在")
        assert exc.status_code == 404
        assert exc.error_code == "NOT_FOUND"
        assert exc.message == "资源不存在"
        assert exc.detail is None

    def test_string_error_code(self) -> None:
        """支持传入纯字符串 error_code（非枚举场景）"""
        exc = AppException(status_code=500, error_code="CUSTOM_ERROR", message="自定义错误")
        assert exc.error_code == "CUSTOM_ERROR"

    def test_exception_is_raiseable(self) -> None:
        """AppException 是标准 Exception 的子类，可以被 raise 捕获"""
        with pytest.raises(AppException):
            raise AppException(status_code=400, error_code=ErrorCode.VALIDATION_ERROR, message="test")

    def test_detail_preserves_complex_data(self) -> None:
        """detail 字段可以承载 list/dict 等复杂结构"""
        exc = AppException(status_code=422, error_code=ErrorCode.VALIDATION_ERROR,
                           message="校验失败", detail=[{"field": "name", "msg": "必填"}])
        assert isinstance(exc.detail, list)
        assert exc.detail[0]["field"] == "name"


# ==================== 集成测试：异常处理器 ====================
class TestExceptionHandlers:
    """通过 TestClient 触发真实请求，验证异常处理器的 JSON 响应格式"""

    def test_app_exception_returns_structured_json(self, client: TestClient) -> None:
        response = client.get("/test/app-error")
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "FORBIDDEN"
        assert body["message"] == "无权访问此资源"
        assert body["detail"]["required_role"] == "admin"

    def test_validation_error_returns_422(self, client: TestClient) -> None:
        response = client.get("/test/validation-error?q=5")  # q < 10，触发验证失败
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert isinstance(body["detail"], list)
        # 验证字段错误信息包含字段名
        assert any("q" in err["field"] for err in body["detail"])

    def test_unhandled_error_returns_500(self, client: TestClient) -> None:
        response = client.get("/test/unhandled-error")
        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "INTERNAL_ERROR"

    def test_nonexistent_route_returns_404(self, client: TestClient) -> None:
        """访问不存在的路由，FastAPI 自动返回 404，由 http_exception_handler 捕获"""
        response = client.get("/test/nonexistent")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "INTERNAL_ERROR"  # Starlette 404 走 StarletteHTTPException 处理
