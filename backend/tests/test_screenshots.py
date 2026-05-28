"""
Screenshot Router — 截图文件服务测试
"""
import os
import tempfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.screenshots import router, SCREENSHOTS_BASE_DIR


class TestScreenshotRouter:
    """截图服务端点"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """创建测试应用和临时截图目录"""
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

        # 临时覆盖 base_dir
        self.temp_dir = tempfile.mkdtemp()
        self._original_dir = SCREENSHOTS_BASE_DIR
        # 使用 monkeypatch 覆盖模块常量
        import app.routers.screenshots as mod
        mod.SCREENSHOTS_BASE_DIR = self.temp_dir

        yield

        # 恢复
        mod.SCREENSHOTS_BASE_DIR = self._original_dir
        # 清理临时目录
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_screenshot_not_found(self):
        """不存在的截图返回 404"""
        resp = self.client.get("/api/screenshots/nonexistent.png?session_id=1")
        assert resp.status_code == 404

    def test_get_screenshot_path_traversal(self):
        """路径遍历攻击被拒绝"""
        # ../ 会被 URL 路由器规范化，返回 404（路由层防护）
        resp = self.client.get("/api/screenshots/../secret.txt?session_id=1")
        assert resp.status_code == 404

    def test_get_screenshot_invalid_filename(self):
        """非法文件名被应用层拦截"""
        # 包含反斜杠（Windows 路径遍历）— 使用查询参数注入场景
        resp = self.client.get("/api/screenshots/test%5Ctest.txt?session_id=1")
        # %5C = \ → 解码后应用层拒绝
        assert resp.status_code == 400

    def test_get_screenshot_success(self):
        """正常获取截图"""
        # 创建截图文件
        session_dir = os.path.join(self.temp_dir, "1")
        os.makedirs(session_dir, exist_ok=True)
        filepath = os.path.join(session_dir, "step_01_goto.png")
        with open(filepath, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # 最小 PNG 头

        resp = self.client.get("/api/screenshots/step_01_goto.png?session_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_get_screenshot_no_session_id(self):
        """缺少 session_id 返回 422"""
        resp = self.client.get("/api/screenshots/test.png")
        assert resp.status_code == 422

    def test_list_empty_session(self):
        """空 Session 的截图列表"""
        resp = self.client.get("/api/screenshots/?session_id=999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["screenshots"] == []

    def test_list_screenshots(self):
        """列出 Session 的所有截图"""
        session_dir = os.path.join(self.temp_dir, "1")
        os.makedirs(session_dir, exist_ok=True)
        for name in ["step_01_goto.png", "step_02_click.png", "step_03_screenshot.png"]:
            with open(os.path.join(session_dir, name), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        resp = self.client.get("/api/screenshots/?session_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["screenshots"][0]["filename"] == "step_01_goto.png"
        assert data["screenshots"][0]["step_number"] == 1
        assert data["screenshots"][0]["action"] == "goto"
