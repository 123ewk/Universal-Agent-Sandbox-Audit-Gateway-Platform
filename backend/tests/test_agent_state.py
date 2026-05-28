"""
Agent 状态模型测试

测试范围：
  - AgentState 创建与验证
  - 状态转换规则
  - StepRecord 属性
  - PlanStep 验证
  - ObservationRecord 字段
  - 费用累计
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from app.agent.state import (
    AgentState,
    AgentStatus,
    ObservationRecord,
    PlanStep,
    StepRecord,
)


class TestAgentStatus:
    """AgentStatus 枚举"""

    def test_all_statuses_exist(self):
        assert AgentStatus.IDLE.value == "idle"
        assert AgentStatus.PLANNING.value == "planning"
        assert AgentStatus.EXECUTING.value == "executing"
        assert AgentStatus.COMPLETED.value == "completed"
        assert AgentStatus.FAILED.value == "failed"

    def test_waiting_approval(self):
        assert AgentStatus.WAITING_APPROVAL.value == "waiting_approval"


class TestPlanStep:
    """PlanStep 模型"""

    def test_create_valid_step(self):
        step = PlanStep(
            step_number=1,
            description="导航到百度",
            skill_name="browser_goto",
            skill_params={"url": "https://www.baidu.com"},
        )
        assert step.step_number == 1
        assert step.skill_name == "browser_goto"
        assert step.skill_params["url"] == "https://www.baidu.com"

    def test_default_values(self):
        step = PlanStep(step_number=1, description="测试", skill_name="test_skill")
        assert step.skill_params == {}
        assert step.expected_outcome == ""
        assert step.requires_approval is False
        assert step.required_tier is None

    def test_step_number_must_be_positive(self):
        with pytest.raises(Exception):
            PlanStep(step_number=0, description="测试", skill_name="test")

    def test_expected_outcome_is_optional(self):
        step = PlanStep(
            step_number=2,
            description="点击搜索",
            skill_name="browser_click",
            expected_outcome="页面跳转到搜索结果",
        )
        assert step.expected_outcome == "页面跳转到搜索结果"


class TestStepRecord:
    """StepRecord 模型"""

    def test_create_record(self):
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")
        record = StepRecord(step_number=1, plan_step=plan, success=True)
        assert record.step_number == 1
        assert record.success is True
        assert record.execution_time_ms == 0

    def test_duration_seconds(self):
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        record = StepRecord(step_number=1, plan_step=plan, execution_time_ms=1500)
        assert record.duration_seconds == 1.5

    def test_is_completed(self):
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        record = StepRecord(step_number=1, plan_step=plan)
        assert record.is_completed is False

        record.finished_at = datetime.now(timezone.utc)
        assert record.is_completed is True

    def test_with_error(self):
        plan = PlanStep(step_number=3, description="点击", skill_name="browser_click")
        record = StepRecord(
            step_number=3,
            plan_step=plan,
            success=False,
            error_message="元素未找到",
        )
        assert record.success is False
        assert record.error_message == "元素未找到"


class TestObservationRecord:
    """ObservationRecord 模型"""

    def test_empty(self):
        obs = ObservationRecord()
        assert obs.summary == ""
        assert obs.interactive_elements == []

    def test_with_data(self):
        obs = ObservationRecord(
            summary="页面包含搜索框和按钮",
            page_title="百度一下",
            page_url="https://www.baidu.com",
            interactive_elements=[
                {"type": "input_text", "text": "搜索", "selector": "#kw"},
                {"type": "button", "text": "百度一下", "selector": "#su"},
            ],
        )
        assert len(obs.interactive_elements) == 2
        assert obs.page_title == "百度一下"

    def test_with_errors(self):
        obs = ObservationRecord(
            summary="页面加载失败",
            errors=["404 页面不存在"],
            warnings=["JavaScript 未启用"],
        )
        assert len(obs.errors) == 1
        assert len(obs.warnings) == 1


class TestAgentState:
    """AgentState 核心状态机"""

    def test_initial_state(self):
        state = AgentState(
            session_id=1,
            task_description="打开百度搜索天气",
        )
        assert state.session_id == 1
        assert state.agent_status == AgentStatus.IDLE
        assert state.current_step_index == 0
        assert state.plan_steps == []
        assert state.execution_history == []

    def test_max_steps_default(self):
        state = AgentState(task_description="测试")
        assert state.max_steps == 50

    def test_max_steps_custom(self):
        state = AgentState(task_description="测试", max_steps=30)
        assert state.max_steps == 30

    def test_max_steps_must_be_positive(self):
        with pytest.raises(Exception):
            AgentState(task_description="测试", max_steps=0)

    def test_max_steps_upper_limit(self):
        with pytest.raises(Exception):
            AgentState(task_description="测试", max_steps=500)

    def test_is_finished(self):
        state = AgentState(task_description="测试")
        assert state.is_finished is False
        state.agent_status = AgentStatus.COMPLETED
        assert state.is_finished is True

    def test_recent_steps_empty(self):
        state = AgentState(task_description="测试")
        assert state.recent_steps == []

    def test_recent_steps_with_data(self):
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        for i in range(7):
            state.execution_history.append(
                StepRecord(step_number=i + 1, plan_step=plan, success=True)
            )
        assert len(state.recent_steps) == 5  # 最近 5 步

    def test_progress_pct_empty_plan(self):
        state = AgentState(task_description="测试")
        assert state.progress_pct == 0.0

    def test_progress_pct(self):
        state = AgentState(task_description="测试")
        state.plan_steps = [
            PlanStep(step_number=i, description="测试", skill_name="test")
            for i in range(1, 5)
        ]
        state.current_step_index = 2
        assert state.progress_pct == 50.0

    def test_current_plan_step(self):
        state = AgentState(task_description="测试")
        state.plan_steps = [
            PlanStep(step_number=1, description="第一步", skill_name="skill_a"),
            PlanStep(step_number=2, description="第二步", skill_name="skill_b"),
        ]
        assert state.current_plan_step is not None
        assert state.current_plan_step.description == "第一步"

    def test_current_plan_step_out_of_bounds(self):
        state = AgentState(task_description="测试")
        state.plan_steps = [PlanStep(step_number=1, description="测试", skill_name="test")]
        state.current_step_index = 5
        assert state.current_plan_step is None

    def test_steps_remaining(self):
        state = AgentState(task_description="测试")
        state.plan_steps = [
            PlanStep(step_number=i, description="测试", skill_name="test")
            for i in range(1, 5)
        ]
        state.current_step_index = 1
        assert state.steps_remaining == 3

    def test_record_step(self):
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        record = StepRecord(
            step_number=1, plan_step=plan, success=True, llm_cost=Decimal("0.001")
        )
        state.record_step(record)
        assert state.total_steps_executed == 1
        assert state.total_llm_cost == Decimal("0.001")

    def test_add_cost(self):
        state = AgentState(task_description="测试")
        state.add_cost(Decimal("0.005"), tokens=1000)
        assert state.total_llm_cost == Decimal("0.005")
        assert state.total_tokens_used == 1000

    def test_transition_to_valid(self):
        state = AgentState(task_description="测试")
        state.transition_to(AgentStatus.PLANNING)
        assert state.agent_status == AgentStatus.PLANNING

    def test_transition_to_invalid(self):
        state = AgentState(task_description="测试", agent_status=AgentStatus.IDLE)
        with pytest.raises(ValueError, match="非法状态转换"):
            state.transition_to(AgentStatus.EXECUTING)  # IDLE → EXECUTING 不是有效转换

    def test_last_step(self):
        state = AgentState(task_description="测试")
        assert state.last_step is None
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        record = StepRecord(step_number=1, plan_step=plan)
        state.record_step(record)
        assert state.last_step is not None
        assert state.last_step.step_number == 1
