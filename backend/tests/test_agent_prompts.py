"""
Prompt 模板测试

测试范围：
  - PromptBuilder 初始化（不同 provider）
  - build_system() 输出
  - build_plan_prompt() 模板格式化
  - build_execute_prompt() 上下文注入
  - build_reflect_prompt() 决策建议
  - 变量注入正确性
"""
import pytest

from app.agent.prompts import PromptBuilder
from app.agent.state import AgentState, PlanStep, StepRecord, ObservationRecord
from app.skills.selector import SkillSelector


class TestPromptBuilder:
    """PromptBuilder 测试"""

    def test_initialization_deepseek(self):
        builder = PromptBuilder(provider="deepseek")
        assert builder.provider == "deepseek"

    def test_initialization_openai(self):
        builder = PromptBuilder(provider="openai")
        assert builder.provider == "openai"

    def test_initialization_claude(self):
        builder = PromptBuilder(provider="claude")
        assert builder.provider == "claude"

    def test_initialization_unknown_falls_back(self):
        builder = PromptBuilder(provider="unknown")
        # 不崩溃, fallback to deepseek
        assert builder.provider == "unknown"

    def test_build_system_deepseek(self):
        builder = PromptBuilder(provider="deepseek")
        system = builder.build_system()
        assert "ShadowOS" in system or "安全沙箱" in system
        assert len(system) > 0

    def test_build_system_openai(self):
        builder = PromptBuilder(provider="openai")
        system = builder.build_system()
        assert "ShadowOS" in system
        assert len(system) > 0

    def test_build_system_claude(self):
        builder = PromptBuilder(provider="claude")
        system = builder.build_system()
        assert "ShadowOS" in system
        assert len(system) > 0

    def test_build_plan_prompt(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(task_description="搜索天气")
        selector = SkillSelector()

        prompt = builder.build_plan_prompt(
            task_description="搜索天气",
            state=state,
            selector=selector,
        )
        assert "搜索天气" in prompt
        assert "browser_goto" in prompt  # CORE skill should be visible
        assert "JSON" in prompt

    def test_build_plan_prompt_with_history(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(task_description="继续搜索")
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")
        state.execution_history.append(
            StepRecord(step_number=1, plan_step=plan, success=True)
        )
        selector = SkillSelector()

        prompt = builder.build_plan_prompt("继续搜索", state, selector)
        assert "browser_goto" in prompt

    def test_build_execute_prompt(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(
            task_description="搜索天气",
            plan_steps=[
                PlanStep(
                    step_number=1,
                    description="导航到百度",
                    skill_name="browser_goto",
                    skill_params={"url": "https://www.baidu.com"},
                ),
            ],
        )
        state.current_step_index = 0
        selector = SkillSelector()

        prompt = builder.build_execute_prompt(state)
        assert "browser_goto" in prompt
        assert "https://www.baidu.com" in prompt

    def test_build_execute_prompt_with_observation(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(
            task_description="搜索天气",
            plan_steps=[
                PlanStep(step_number=1, description="导航", skill_name="browser_goto"),
                PlanStep(step_number=2, description="点击搜索", skill_name="browser_click"),
            ],
        )
        state.current_step_index = 1  # 当前在步骤 2
        state.last_observation = ObservationRecord(
            summary="页面加载完成，显示搜索框",
            page_title="百度首页",
        )

        prompt = builder.build_execute_prompt(state)
        assert "browser_click" in prompt
        assert "页面加载完成" in prompt

    def test_build_execute_prompt_no_current_step(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(task_description="测试")
        # 当前步骤不存在
        prompt = builder.build_execute_prompt(state)
        assert "没有可执行的步骤" in prompt

    def test_build_execute_prompt_with_skill_doc(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(
            task_description="测试",
            plan_steps=[
                PlanStep(step_number=1, description="导航", skill_name="browser_goto"),
            ],
        )
        doc = "# Skill: browser_goto\n\n导航到指定URL\n\n## Parameters\n- url: string"
        prompt = builder.build_execute_prompt(state, skill_doc=doc)
        assert "browser_goto" in prompt
        assert "Parameters" in prompt

    def test_build_reflect_prompt(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(
            task_description="搜索天气",
            plan_steps=[
                PlanStep(step_number=1, description="导航", skill_name="browser_goto"),
                PlanStep(step_number=2, description="输入", skill_name="browser_type"),
                PlanStep(step_number=3, description="截图", skill_name="browser_screenshot"),
            ],
        )
        state.current_step_index = 1
        plan = state.plan_steps[0]
        state.execution_history.append(
            StepRecord(step_number=1, plan_step=plan, success=True, execution_time_ms=100)
        )

        prompt = builder.build_reflect_prompt(state)
        assert "browser_goto" in prompt
        assert "成功" in prompt
        # 应包含剩余步骤描述
        assert "截图" in prompt

    def test_build_reflect_prompt_no_last_step(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(task_description="测试")
        prompt = builder.build_reflect_prompt(state)
        assert "没有需要评估的步骤" in prompt

    def test_build_reflect_prompt_with_error(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(
            task_description="测试",
            plan_steps=[
                PlanStep(step_number=1, description="导航", skill_name="browser_goto"),
            ],
        )
        plan = state.plan_steps[0]
        state.execution_history.append(
            StepRecord(
                step_number=1,
                plan_step=plan,
                success=False,
                error_message="连接超时",
            )
        )

        prompt = builder.build_reflect_prompt(state)
        assert "失败" in prompt or "ERR" in prompt.upper()
        assert "连接超时" in prompt

    def test_format_tools_for_plan(self):
        from app.skills import init_skills
        init_skills()
        builder = PromptBuilder(provider="deepseek")
        selector = SkillSelector()
        tools_text = builder._format_tools_for_plan(selector)
        assert "browser_goto" in tools_text
        assert "browser_screenshot" in tools_text
        assert len(tools_text) > 0

    def test_format_history_empty(self):
        builder = PromptBuilder(provider="deepseek")
        state = AgentState(task_description="测试")
        text = builder._format_history_for_plan(state)
        assert "无历史" in text
