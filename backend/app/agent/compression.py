"""
ContextCompressor — 上下文裁剪与压缩器

设计动机：
  Agent 执行数十步后，execution_history 可能积累大量 StepRecord，
  Working Context 超出 ~4k tokens 限制。ContextCompressor 负责：
    1. Working Context 裁剪：保留最近 3-5 步，超出时移除最早步骤
    2. Summary 增量更新：每步执行后追加更新摘要，不从头重新总结
    3. 长期记忆持久化：旧步骤的关键信息转移到 Memory（向量存储）

裁剪策略：
  — 保留最近 N 步的完整 StepRecord（默认 5 步）
  — 更早的步骤压缩为摘要，原始数据从 Working Context 中移除
  — 压缩后的摘要追加到 state.observation_summary 中

使用方式：
  compressor = ContextCompressor(max_working_steps=5, max_working_tokens=4000)
  state = compressor.compress(state)
"""
import logging
from typing import Optional

from app.agent.state import AgentState, StepRecord

logger = logging.getLogger(__name__)

# 估算：一个 StepRecord 转换为字符串后约多少 tokens
_ESTIMATED_TOKENS_PER_STEP = 800


class ContextCompressor:
    """
    上下文裁剪器

    两种裁剪策略：
      Soft Trim:  保留最近 max_working_steps 步，裁剪更早的
      Hard Trim:  总 token 超限时进一步裁剪到最少 3 步
    """

    def __init__(
        self,
        max_working_steps: int = 5,
        max_working_tokens: int = 4000,
        min_working_steps: int = 3,
    ) -> None:
        self.max_working_steps = max_working_steps
        self.max_working_tokens = max_working_tokens
        self.min_working_steps = min_working_steps

    # ================================================================
    # 主入口
    # ================================================================

    def compress(self, state: AgentState) -> AgentState:
        """
        压缩 AgentState 的 Working Context

        步骤：
          1. 估算当前 Working Context 的 token 数量
          2. 如果超限 → 裁剪最早步骤
          3. 裁剪掉的步骤 → 提取摘要信息 → 追加到 observation_summary
          4. 如果有步骤需要长期记忆 → 标记待持久化

        Returns:
            修改后的 AgentState（原地修改 + 返回）
        """
        history = state.execution_history
        if not history:
            return state

        # 计算当前状态
        current_tokens = self._estimate_working_tokens(history)
        current_steps = len(history)

        logger.debug(
            "压缩检查: steps=%d, estimated_tokens=%d, "
            "limit_steps=%d, limit_tokens=%d",
            current_steps, current_tokens,
            self.max_working_steps, self.max_working_tokens,
        )

        # 不需要裁剪
        if current_steps <= self.max_working_steps and current_tokens <= self.max_working_tokens:
            return state

        # 确定保留步数
        keep_count = self._determine_keep_count(history, current_tokens)

        if keep_count >= current_steps:
            return state  # 无需裁剪

        # 裁剪：前 N 步压缩为摘要
        trimmed = history[:-keep_count] if keep_count > 0 else history[:]
        kept = history[-keep_count:] if keep_count > 0 else []

        # 将裁剪掉的步骤生成增量摘要
        if trimmed:
            incremental_summary = self._summarize_steps(trimmed)
            if state.observation_summary:
                state.observation_summary += "\n" + incremental_summary
            else:
                state.observation_summary = incremental_summary

            # 标记需要持久化到长期记忆的步骤
            self._flag_for_memory(trimmed)

            logger.info(
                "上下文已裁剪: %d 步 → 保留 %d 步, "
                "摘要追加 %d chars, 标记 %d 步待持久化",
                current_steps, keep_count,
                len(incremental_summary), len(trimmed),
            )

        return state

    # ================================================================
    # 裁剪策略
    # ================================================================

    def _determine_keep_count(
        self, history: list[StepRecord], current_tokens: int
    ) -> int:
        """
        确定保留多少步

        策略优先级：
          1. 如果步数超限 → 保留 max_working_steps
          2. 如果 token 超限 → 递减保留步数直到 token 不超限（最低 min_working_steps）
        """
        # 步数裁剪
        if len(history) > self.max_working_steps:
            keep = self.max_working_steps
        else:
            keep = len(history)

        # Token 裁剪：如果保留 keep 步仍然超限 → 递减
        while keep > self.min_working_steps:
            estimated = self._estimate_working_tokens(history[-keep:])
            if estimated <= self.max_working_tokens:
                break
            keep -= 1

        return max(keep, self.min_working_steps)

    # ================================================================
    # 摘要生成
    # ================================================================

    def _summarize_steps(self, steps: list[StepRecord]) -> str:
        """
        将多个步骤压缩为简洁摘要

        不求自然语言优美，求信息密度高：
          "[Step 1] browser_goto(baidu.com) ✓ → 页面加载成功
           [Step 2] browser_type(#kw, '天气') ✓ → 输入完成
           [Step 3] browser_click(#su) ✗ → 超时"
        """
        lines: list[str] = []
        for step in steps:
            outcome = "OK" if step.success else "ERR"
            skill = step.plan_step.skill_name
            params = step.plan_step.skill_params
            # 简短参数
            params_str = ", ".join(
                f"{k}={str(v)[:40]}" for k, v in list(params.items())[:2]
            )
            err = f": {step.error_message[:60]}" if step.error_message else ""
            obs = ""
            if step.observation_structured:
                obs_text = step.observation_structured.get("summary", "")
                if obs_text:
                    obs = f" | {obs_text[:80]}"
            lines.append(
                f"[S{step.step_number}] {skill}({params_str}) {outcome}{err}{obs}"
            )
        return "历史步骤摘要:\n" + "\n".join(lines)

    # ================================================================
    # 长期记忆标记
    # ================================================================

    def _flag_for_memory(self, steps: list[StepRecord]) -> None:
        """
        标记需要持久化到向量记忆的步骤

        策略：
          - 失败的步骤 → 高优先级持久化（供后续参考）
          - 涉及审批的步骤 → 持久化（合规需求）
          - 普通成功步骤 → 不持久化（减少噪音）
        """
        for step in steps:
            if not step.success:
                step.observation_structured = step.observation_structured or {}
                step.observation_structured["_persist_to_memory"] = True
                step.observation_structured["_memory_type"] = "error"
            if step.required_approval:
                step.observation_structured = step.observation_structured or {}
                step.observation_structured["_persist_to_memory"] = True
                step.observation_structured["_memory_type"] = "decision"

    # ================================================================
    # Token 估算
    # ================================================================

    def _estimate_working_tokens(self, steps: list[StepRecord]) -> int:
        """
        估算步骤列表转换为文本后的 token 数量

        粗略估计：每个 StepRecord ≈ 800 tokens
        包含：step_number, description, skill_name, params, result, error, observation
        """
        if not steps:
            return 0
        # 基础估算
        base = len(steps) * _ESTIMATED_TOKENS_PER_STEP
        # 有 observation 的步骤额外加 200 tokens
        for step in steps:
            if step.observation_structured:
                base += 200
            if step.error_message:
                base += len(step.error_message) // 4
        return base

    # ================================================================
    # 公共方法：增量摘要更新
    # ================================================================

    def update_summary(
        self,
        state: AgentState,
        step: StepRecord,
        observation_summary: str = "",
    ) -> str:
        """
        增量更新执行摘要（不从头重新总结）

        每步执行后调用，追加一条新摘要行。
        这是性能关键优化 — 避免每次重新总结所有步骤。

        Returns:
            更新后的完整 observation_summary 字符串
        """
        outcome = "OK" if step.success else "ERR"
        line = f"[S{step.step_number}] {step.plan_step.skill_name}: {outcome}"
        if observation_summary:
            line += f" — {observation_summary}"
        if step.error_message:
            line += f" (错误: {step.error_message[:80]})"

        if state.observation_summary:
            state.observation_summary += "\n" + line
        else:
            state.observation_summary = line

        return state.observation_summary
