"""
Sandbox 沙箱引擎测试

测试范围：
  - PageInfo / ActionResult 模型
  - SandboxSecurity URL 检查 + 高危行为检测
  - ScreenshotManager 路径管理
  - SandboxEngine (Mock 模式，不启动真实浏览器)
  - 向后兼容：Skills 无 engine 时降级 Mock
"""
import os
import tempfile
import pytest

from app.sandbox.models import PageInfo, ActionResult
from app.sandbox.security import SandboxSecurity, SandboxSecurityError, SecurityCheck
from app.sandbox.screenshot import ScreenshotManager
from app.skills.base import SkillContext
from app.skills.browser import GotoSkill, ClickSkill, TypeSkill, ScreenshotSkill, ExtractTextSkill


class TestPageInfo:
    """PageInfo 数据模型"""

    def test_empty(self):
        info = PageInfo()
        assert info.url == ""
        assert info.title == ""
        assert info.text_preview == ""
        assert info.element_count == 0

    def test_with_data(self):
        info = PageInfo(
            url="https://example.com",
            title="Example",
            cleaned_text="Hello World " * 50,
            interactive_elements=[
                {"type": "button", "text": "Click", "selector": "#btn"},
                {"type": "input_text", "text": "Search", "selector": "#q"},
            ],
            screenshot_path="/tmp/screenshot.png",
            status_code=200,
        )
        assert info.url == "https://example.com"
        assert info.title == "Example"
        assert len(info.text_preview) <= 200
        assert info.element_count == 2

    def test_to_dict(self):
        info = PageInfo(url="https://test.com", title="Test")
        d = info.to_dict()
        assert d["url"] == "https://test.com"
        assert d["title"] == "Test"


class TestActionResult:
    """ActionResult 数据模型"""

    def test_ok(self):
        result = ActionResult.ok(data={"key": "value"}, execution_time_ms=100)
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.execution_time_ms == 100

    def test_fail(self):
        result = ActionResult.fail("something went wrong")
        assert result.success is False
        assert result.error == "something went wrong"

    def test_default(self):
        result = ActionResult()
        assert result.success is True
        assert result.data is None


class TestSandboxSecurity:
    """SandboxSecurity 安全引擎"""

    def test_allow_normal_url(self):
        sec = SandboxSecurity()
        check = sec.check_url("https://www.baidu.com")
        assert check.allowed is True
        assert check.is_blocked is False

    def test_block_file_url(self):
        sec = SandboxSecurity()
        with pytest.raises(SandboxSecurityError):
            sec.check_url("file:///etc/passwd")

    def test_block_chrome_url(self):
        sec = SandboxSecurity()
        with pytest.raises(SandboxSecurityError):
            sec.check_url("chrome://settings")

    def test_block_about_blank(self):
        sec = SandboxSecurity()
        with pytest.raises(SandboxSecurityError):
            sec.check_url("about:blank")

    def test_block_javascript_url(self):
        sec = SandboxSecurity()
        with pytest.raises(SandboxSecurityError):
            sec.check_url("javascript:alert(1)")

    def test_high_risk_bank_url(self):
        sec = SandboxSecurity()
        check = sec.check_url("https://bank.com/transfer?amount=100")
        assert check.risk_score >= 30
        assert len(check.reasons) > 0

    def test_high_risk_admin_url(self):
        sec = SandboxSecurity()
        check = sec.check_url("https://admin.example.com/dashboard")
        assert check.risk_score >= 30

    def test_custom_blocklist(self):
        sec = SandboxSecurity(url_blocklist=["https://evil.com/*"])
        with pytest.raises(SandboxSecurityError):
            sec.check_url("https://evil.com/phishing")

    def test_check_action_click(self):
        sec = SandboxSecurity()
        check = sec.check_action("click", selector="#submit-btn")
        # submit 关键词触发高危
        assert check.requires_approval is True

    def test_check_action_type(self):
        sec = SandboxSecurity()
        check = sec.check_action("type", selector="#search", text="hello")
        assert check.requires_approval is False

    def test_check_action_download(self):
        sec = SandboxSecurity()
        check = sec.check_action("download", selector="#dl-btn")
        assert check.requires_approval is True

    def test_check_action_payment(self):
        sec = SandboxSecurity()
        check = sec.check_action("click", text="confirm payment of $100")
        assert check.requires_approval is True

    def test_check_action_safe(self):
        sec = SandboxSecurity()
        check = sec.check_action("click", selector="#nav-home")
        assert check.requires_approval is False
        assert check.is_blocked is False

    def test_route_interception_disabled(self):
        sec = SandboxSecurity(enable_route_interception=False)
        assert sec.enable_route_interception is False


class TestScreenshotManager:
    """ScreenshotManager 截图管理"""

    def test_build_filename(self):
        mgr = ScreenshotManager()
        name = mgr.build_filename(1, "goto")
        assert name == "step_01_goto.png"

    def test_build_filename_padded(self):
        mgr = ScreenshotManager()
        name = mgr.build_filename(10, "click")
        assert name == "step_10_click.png"

    def test_get_session_dir(self):
        mgr = ScreenshotManager(base_dir="data/screenshots")
        path = mgr.get_session_dir(42)
        assert "data/screenshots" in path
        assert "42" in path

    def test_ensure_session_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ScreenshotManager(base_dir=tmp)
            dir_path = mgr.ensure_session_dir(1)
            assert os.path.isdir(dir_path)

    def test_get_session_screenshots_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ScreenshotManager(base_dir=tmp)
            files = mgr.get_session_screenshots(999)
            assert files == []

    def test_cleanup_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = ScreenshotManager(base_dir=tmp)
            mgr.ensure_session_dir(1)
            # 创建假文件
            with open(os.path.join(mgr.get_session_dir(1), "test.png"), "w") as f:
                f.write("fake")
            assert len(mgr.get_session_screenshots(1)) == 1
            mgr.cleanup_session(1)
            assert not os.path.isdir(mgr.get_session_dir(1))


class TestSkillBackwardCompat:
    """Skills 无沙箱时返回明确错误（不再降级 Mock）"""

    @pytest.mark.asyncio
    async def test_goto_without_engine(self):
        """无沙箱时 goto 应返回失败，提示沙箱未初始化"""
        ctx = SkillContext(session_id=1)
        skill = GotoSkill()
        result = await skill.execute(ctx, url="https://example.com")
        assert result.success is False
        assert "沙箱未初始化" in result.error

    @pytest.mark.asyncio
    async def test_goto_without_engine_missing_url(self):
        """缺少 url 参数返回失败（参数校验优先于沙箱检查）"""
        ctx = SkillContext()
        skill = GotoSkill()
        result = await skill.execute(ctx)
        assert result.success is False
        assert "url" in result.error.lower()

    @pytest.mark.asyncio
    async def test_click_without_engine(self):
        """无沙箱时 click 应返回失败"""
        ctx = SkillContext()
        skill = ClickSkill()
        result = await skill.execute(ctx, selector="#btn")
        assert result.success is False
        assert "沙箱未初始化" in result.error

    @pytest.mark.asyncio
    async def test_click_without_engine_missing_selector(self):
        """缺少 selector 返回失败（参数校验优先）"""
        ctx = SkillContext()
        skill = ClickSkill()
        result = await skill.execute(ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_type_without_engine(self):
        """无沙箱时 type 应返回失败"""
        ctx = SkillContext()
        skill = TypeSkill()
        result = await skill.execute(ctx, selector="#q", text="hello")
        assert result.success is False
        assert "沙箱未初始化" in result.error

    @pytest.mark.asyncio
    async def test_screenshot_without_engine(self):
        """无沙箱时 screenshot 应返回失败"""
        ctx = SkillContext()
        skill = ScreenshotSkill()
        result = await skill.execute(ctx)
        assert result.success is False
        assert "沙箱未初始化" in result.error

    @pytest.mark.asyncio
    async def test_extract_text_without_engine(self):
        """无沙箱时 extract_text 应返回失败"""
        ctx = SkillContext()
        skill = ExtractTextSkill()
        result = await skill.execute(ctx, selector="body")
        assert result.success is False
        assert "沙箱未初始化" in result.error


class TestSandboxEngineIntegration:
    """SandboxEngine 集成 — Mock Provider 测试核心逻辑"""

    @pytest.mark.asyncio
    async def test_engine_requires_create_context_first(self):
        """未 create_context 就调用 navigate，返回失败但不崩溃"""
        from app.sandbox.provider import SandboxProvider

        class MockProvider(SandboxProvider):
            async def launch(self): pass
            async def create_context(self, session_id, **kwargs): return None
            async def destroy_context(self, session_id): pass
            async def shutdown(self): pass
            def get_active_sessions(self): return []
            def get_session_count(self): return 0

        from app.sandbox.engine import SandboxEngine
        engine = SandboxEngine(provider=MockProvider(), session_id=1)
        result = await engine.navigate("https://example.com")
        assert result.success is False
        assert "未初始化" in result.error
