"""
App Factory — create_app() 集成测试
"""
import pytest

# 检查 asyncpg 是否可用（DB 测试需要）
try:
    import asyncpg  # noqa: F401
    HAS_DB = True
except ImportError:
    HAS_DB = False


@pytest.mark.skipif(not HAS_DB, reason="asyncpg 未安装")
class TestAppFactory:
    """应用工厂集成测试（需要数据库连接）"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from app.app_factory import create_app
        self.app = create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_cors_headers(self):
        resp = self.client.options(
            "/api/v1/tasks",
            headers={"Origin": "http://localhost:5173"},
        )
        assert resp.status_code in (200, 405)
        if resp.status_code == 200:
            assert "access-control-allow-origin" in resp.headers

    def test_tasks_list_endpoint(self):
        resp = self.client.get("/api/v1/tasks")
        assert resp.status_code != 404

    def test_screenshots_list_endpoint(self):
        resp = self.client.get("/api/screenshots/?session_id=999")
        assert resp.status_code == 200

    def test_approvals_pending_endpoint(self):
        resp = self.client.get("/api/v1/approvals/pending")
        assert resp.status_code == 200

    def test_404_for_unknown_route(self):
        resp = self.client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        assert "code" in resp.json()

    def test_swagger_available(self):
        resp = self.client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema(self):
        resp = self.client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        tags = {t["name"] for t in schema.get("tags", [])}
        assert "Agent" in tags
        assert "Approval" in tags
        assert "Screenshots" in tags
