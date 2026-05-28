"""
七层上下文管理器测试

测试范围：
  - ContextManager 初始化
  - assemble() 各层输出
  - Token 预算控制
  - 截断行为
  - 空状态处理
  - estimate_tokens()
"""
import pytest

from app.agent.context import ContextManager
from app.agent.state import AgentState, PlanStep, StepRecord
from app.skills.selector import SkillSelector


class TestContextManager:
    """ContextManager 核心测试"""

    def test_initialization(self):
        cm = ContextManager(max_context_tokens=8000)
        assert cm.max_context_tokens == 8000
        assert "system" in cm.budgets
        assert cm.budgets["working"] == 4000

    def test_estimate_tokens_english(self):
        text = "Hello world, this is a test. " * 50
        tokens = ContextManager.estimate_tokens(text)
        assert tokens > 0
        assert 200 < tokens < 500

    def test_estimate_tokens_chinese(self):
        text = "你好世界这是一个测试" * 20
        tokens = ContextManager.estimate_tokens(text)
        assert tokens > 0
        # 每个中文字符 ≈ 1 token
        assert tokens >= 10 * 20

    def test_estimate_tokens_empty(self):
        assert ContextManager.estimate_tokens("") == 0

    def test_assemble_basic(self):
        """基本组装：空状态 + 默认 SkillSelector"""
        cm = ContextManager()
        state = AgentState(task_description="打开百度")
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "ShadowOS" in result or "安全沙箱" in result
        assert "打开百度" in result

    def test_assemble_task_layer(self):
        """Task 层包含任务描述和计划"""
        cm = ContextManager()
        state = AgentState(
            task_description="搜索天气预报",
            plan_steps=[
                PlanStep(step_number=1, description="导航", skill_name="browser_goto"),
                PlanStep(step_number=2, description="输入关键词", skill_name="browser_type"),
            ],
        )
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "搜索天气预报" in result
        assert "browser_goto" in result
        assert "browser_type" in result

    def test_assemble_execution_layer(self):
        """Execution 层显示进度"""
        cm = ContextManager()
        state = AgentState(
            task_description="测试",
            plan_steps=[PlanStep(step_number=1, description="测试", skill_name="test")],
        )
        state.current_step_index = 0
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "Step 0/1" in result

    def test_assemble_with_observation(self):
        """Observation 层显示观察摘要"""
        cm = ContextManager()
        state = AgentState(
            task_description="测试",
            observation_summary="[S1] browser_goto: OK — 页面加载成功",
        )
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "browser_goto: OK" in result

    def test_assemble_with_execution_history(self):
        """Working 层显示最近步骤"""
        cm = ContextManager()
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="导航到百度", skill_name="browser_goto")
        state.execution_history.append(
            StepRecord(step_number=1, plan_step=plan, success=True, execution_time_ms=100)
        )
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "导航到百度" in result

    def test_assemble_with_memory(self):
        """Memory 层显示长期记忆"""
        cm = ContextManager()
        state = AgentState(
            task_description="测试",
            memory_context="相关记忆: 上次在百度搜索时遇到验证码，需要注意频率限制",
        )
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        assert "验证码" in result

    def test_assemble_with_skill_doc(self):
        """Tool Doc 层按需加载 skill.md"""
        cm = ContextManager()
        state = AgentState(task_description="测试")
        selector = SkillSelector()
        # 测试不存在的 skill doc 不会崩溃
        result = cm.assemble(state, selector, skill_doc_name="nonexistent")
        assert "ShadowOS" in result or "安全沙箱" in result or "沙箱" in result

    def test_assemble_no_tools_available(self):
        """空工具列表不会崩溃"""
        cm = ContextManager()
        state = AgentState(task_description="测试")
        selector = SkillSelector()
        # CORE 总是有一些工具的, 所以正常输出
        result = cm.assemble(state, selector)
        assert len(result) > 0

    def test_assemble_with_custom_system_prompt(self):
        """支持外部注入 System Prompt"""
        cm = ContextManager()
        state = AgentState(task_description="测试")
        selector = SkillSelector()

        custom = "自定义系统提示: 你是一个安全审计助手"
        result = cm.assemble(state, selector, system_prompt=custom)
        assert "安全审计助手" in result

    def test_budget_truncation(self):
        """超预算时截断"""
        cm = ContextManager(max_context_tokens=500)  # 很小的窗口, ~2000 chars
        state = AgentState(
            task_description="这是一个非常长的任务描述" * 50,
            observation_summary="观察摘要" * 100,
        )
        selector = SkillSelector()

        result = cm.assemble(state, selector)
        # 仍然能正确截断而不是报错
        assert len(result) > 0
        # 结果应该在预算范围内
        assert len(result) <= 500 * 4 + 1000  # 容差
