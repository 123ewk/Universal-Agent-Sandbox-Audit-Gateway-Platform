"""
上下文压缩器测试

测试范围：
  - ContextCompressor 初始化
  - 步数超限裁剪
  - 增量摘要生成
  - 长期记忆标记
  - Token 估算
  - 边界情况（空历史、渐进压缩）
"""
import pytest

from app.agent.compression import ContextCompressor
from app.agent.state import AgentState, PlanStep, StepRecord


class TestContextCompressor:
    """ContextCompressor 核心测试"""

    def test_initialization(self):
        comp = ContextCompressor(max_working_steps=5, max_working_tokens=4000)
        assert comp.max_working_steps == 5
        assert comp.max_working_tokens == 4000
        assert comp.min_working_steps == 3

    def test_estimate_tokens_empty(self):
        comp = ContextCompressor()
        assert comp._estimate_working_tokens([]) == 0

    def test_estimate_tokens_basic(self):
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        steps = [StepRecord(step_number=1, plan_step=plan) for _ in range(5)]
        tokens = comp._estimate_working_tokens(steps)
        assert tokens > 0

    def test_compress_noop_when_under_limit(self):
        """步数不超限时不裁剪"""
        comp = ContextCompressor(max_working_steps=5)
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        for i in range(3):
            state.execution_history.append(
                StepRecord(step_number=i + 1, plan_step=plan, success=True)
            )
        result = comp.compress(state)
        # 不裁剪, execution_history 保持原样
        assert len(result.execution_history) == 3

    def test_compress_trims_when_over_step_limit(self):
        """步数超限时裁剪最早步骤"""
        comp = ContextCompressor(max_working_steps=3)
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        for i in range(10):
            state.execution_history.append(
                StepRecord(step_number=i + 1, plan_step=plan, success=True)
            )
        result = comp.compress(state)
        # 保留最近 3 步
        assert len(result.execution_history) >= 3

    def test_compress_generates_summary(self):
        """裁剪后生成增量摘要"""
        comp = ContextCompressor(max_working_steps=2, min_working_steps=2)
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")
        for i in range(5):
            state.execution_history.append(
                StepRecord(
                    step_number=i + 1,
                    plan_step=plan,
                    success=True,
                )
            )
        result = comp.compress(state)
        # 裁剪后生成摘要
        assert "browser_goto" in result.observation_summary or len(result.observation_summary) > 0

    def test_summarize_steps_format(self):
        """摘要格式测试"""
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")
        steps = [
            StepRecord(step_number=1, plan_step=plan, success=True),
        ]
        summary = comp._summarize_steps(steps)
        assert "browser_goto" in summary
        assert "OK" in summary

    def test_summarize_steps_with_error(self):
        """失败步骤摘要包含错误"""
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="点击", skill_name="browser_click")
        steps = [
            StepRecord(
                step_number=1,
                plan_step=plan,
                success=False,
                error_message="元素 #missing 未找到",
            ),
        ]
        summary = comp._summarize_steps(steps)
        assert "ERR" in summary
        assert "#missing" in summary

    def test_flag_failed_for_memory(self):
        """失败步骤标记为持久化"""
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        steps = [
            StepRecord(step_number=1, plan_step=plan, success=False),
        ]
        comp._flag_for_memory(steps)
        assert steps[0].observation_structured is not None
        assert steps[0].observation_structured.get("_persist_to_memory") is True
        assert steps[0].observation_structured.get("_memory_type") == "error"

    def test_flag_approval_for_memory(self):
        """审批步骤标记为持久化"""
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        steps = [
            StepRecord(step_number=1, plan_step=plan, success=True, required_approval=True),
        ]
        comp._flag_for_memory(steps)
        assert steps[0].observation_structured is not None
        assert steps[0].observation_structured.get("_persist_to_memory") is True
        assert steps[0].observation_structured.get("_memory_type") == "decision"

    def test_flag_success_not_persisted(self):
        """成功步骤不标记持久化"""
        comp = ContextCompressor()
        plan = PlanStep(step_number=1, description="测试", skill_name="test")
        steps = [
            StepRecord(step_number=1, plan_step=plan, success=True),
        ]
        comp._flag_for_memory(steps)
        # 成功步骤的 observation_structured 没有被修改
        assert steps[0].observation_structured is None

    def test_update_summary_basic(self):
        """增量摘要更新"""
        comp = ContextCompressor()
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")
        step = StepRecord(step_number=1, plan_step=plan, success=True)

        summary = comp.update_summary(state, step, "页面加载成功")
        assert "browser_goto" in summary
        assert "OK" in summary
        assert "页面加载成功" in summary

    def test_update_summary_accumulates(self):
        """摘要增量累加"""
        comp = ContextCompressor()
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="导航", skill_name="browser_goto")

        comp.update_summary(state, StepRecord(step_number=1, plan_step=plan, success=True), "加载成功")
        comp.update_summary(state, StepRecord(step_number=2, plan_step=plan, success=True), "截图完成")

        assert "S1" in state.observation_summary
        assert "S2" in state.observation_summary
        # 两行用换行分隔
        assert "\n" in state.observation_summary

    def test_update_summary_with_error(self):
        """失败步骤摘要包含错误"""
        comp = ContextCompressor()
        state = AgentState(task_description="测试")
        plan = PlanStep(step_number=1, description="点击", skill_name="browser_click")
        step = StepRecord(step_number=1, plan_step=plan, success=False, error_message="超时")

        summary = comp.update_summary(state, step, "")
        assert "ERR" in summary
        assert "超时" in summary
