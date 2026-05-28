"""
ContextManager — 七层上下文管理器

设计动机：
  LLM 上下文窗口很贵（GPT-4o $5/1M input tokens），每多 1000 token 都是成本。
  传统做法是直接把所有状态塞进 Prompt，但原始 HTML、完整日志、大段 JSON 会迅速
  耗尽上下文窗口。ContextManager 的核心职责是：
    系统决定什么进上下文，不是 LLM 决定。
    原始数据绝不进 Prompt（外部存储 → 摘要 + 引用）。

七层上下文架构：
  Layer 1 — System:   固定的系统身份与规则（始终加载，~300 tokens）
  Layer 2 — Task:     当前任务目标与执行计划（始终加载）
  Layer 3 — Working:  最近 3-5 步的详细记录（~4k tokens，动态裁剪）
  Layer 4 — Observation: 结构化观察摘要（增量更新，不重复加载原始 DOM）
  Layer 5 — Memory:   向量检索的长期记忆（语义相关片段）
  Layer 6 — Tool:     当前可用的 Skill schema + 被选中 Skill 的 skill.md
  Layer 7 — Execution: 步骤计数与进度（~50 tokens，始终加载）

组装优先级：
  System > Task > Execution > Observation > Working > Memory > Tool(Doc)
  越靠前的层越优先保留，超出上下文窗口时从后往前裁剪。

使用方式：
  ctx_mgr = ContextManager(max_context_tokens=8000)
  prompt = ctx_mgr.assemble(state, selector)
"""
import logging
from typing import Optional

from app.agent.state import AgentState, ObservationRecord, StepRecord
from app.skills.registry import registry
from app.skills.selector import SkillSelector

logger = logging.getLogger(__name__)

# 各层的估算 Token 上限（字符数 ≈ tokens × 4）
_ESTIMATED_CHARS_PER_TOKEN = 4


class ContextManager:
    """
    七层上下文管理器

    核心铁律：
      原始数据不进 Prompt — HTML/日志/大JSON → 外部存储 → 摘要 + 引用。
      系统决定什么进上下文 — 不是 LLM 决定。

    上下文窗口分配（默认 8000 tokens ≈ 32000 chars）：
      System:     ~1200 chars (300 tokens)  — 固定
      Task:       ~2000 chars (500 tokens)  — 任务 + 计划
      Execution:  ~200 chars  (50 tokens)   — 进度
      Observation:~800 chars  (200 tokens)  — 结构摘要
      Working:    ~16000 chars (4000 tokens) — 最近步骤
      Memory:     ~3200 chars (800 tokens)  — 长期记忆
      Tool(Schema):~1600 chars (400 tokens) — 可用工具列表
      Tool(Doc):  ~4800 chars (1200 tokens) — skill.md 按需
      Reserve:    ~2200 chars (550 tokens)  — 安全余量
    """

    def __init__(self, max_context_tokens: int = 8000) -> None:
        self.max_context_tokens = max_context_tokens
        self.max_chars = max_context_tokens * _ESTIMATED_CHARS_PER_TOKEN

        # 各层预算（tokens）
        self.budgets = {
            "system": 300,
            "task": 500,
            "execution": 50,
            "observation": 200,
            "working": 4000,
            "memory": 800,
            "tool_schema": 400,
            "tool_doc": 1200,
            "reserve": 550,
        }

    # ================================================================
    # 组装入口
    # ================================================================

    def assemble(
        self,
        state: AgentState,
        selector: SkillSelector,
        system_prompt: str = "",
        skill_doc_name: Optional[str] = None,
    ) -> str:
        """
        组装最终的 LLM 上下文 Prompt

        按优先级组装各层，超出预算时从低优先级层裁剪。

        Args:
            state:           当前 AgentState
            selector:        SkillSelector（决定了哪些 Skills 可见）
            system_prompt:   外部注入的系统提示词模板
            skill_doc_name:  按需加载的 skill.md 名称（如 "browser_click"）

        Returns:
            组装好的完整 Prompt 字符串
        """
        layers: list[tuple[str, str, int]] = []  # (name, content, priority)

        # L1: System（优先级 7 — 最高）
        system_content = system_prompt or self._default_system_prompt()
        layers.append(("system", system_content, 7))

        # L2: Task（优先级 6）
        task_content = self._build_task_layer(state)
        layers.append(("task", task_content, 6))

        # L7: Execution（优先级 5 — 很轻量，但重要）
        exec_content = self._build_execution_layer(state)
        layers.append(("execution", exec_content, 5))

        # L4: Observation（优先级 4）
        obs_content = self._build_observation_layer(state)
        layers.append(("observation", obs_content, 4))

        # L5: Memory（优先级 3）
        mem_content = self._build_memory_layer(state)
        layers.append(("memory", mem_content, 3))

        # L3: Working（优先级 2 — 最大的一块）
        working_content = self._build_working_layer(state)
        layers.append(("working", working_content, 2))

        # L6: Tool Schema（优先级 1 — 工具列表，轻量但必须）
        tool_schema_content = self._build_tool_schema_layer(selector)
        layers.append(("tool_schema", tool_schema_content, 1))

        # Tool Doc（优先级 0 — 按需加载，最大但最不重要）
        tool_doc_content = self._build_tool_doc_layer(selector, skill_doc_name)
        layers.append(("tool_doc", tool_doc_content, 0))

        # 按优先级排序（高优先级在前），逐层累加
        layers.sort(key=lambda x: x[2], reverse=True)

        assembled: list[str] = []
        used_chars = 0

        for name, content, priority in layers:
            if not content:
                continue
            content_chars = len(content)
            budget_chars = self.budgets.get(name, 500) * _ESTIMATED_CHARS_PER_TOKEN
            remaining = self.max_chars - used_chars

            if remaining <= 0:
                logger.warning("上下文窗口已满，跳过层: %s (优先级=%d)", name, priority)
                break

            # 在预算和剩余空间之间取最小值
            allowed = min(budget_chars, remaining)

            if content_chars > allowed:
                # 需要截断：保留前半部分（更重要的内容通常在前）
                truncated = content[:allowed - 100] + "\n\n[... 上下文已截断 ...]"
                assembled.append(truncated)
                used_chars += len(truncated)
                logger.info(
                    "层 '%s' 被截断: %d → %d chars (预算=%d, 剩余=%d)",
                    name, content_chars, len(truncated), budget_chars, remaining,
                )
            else:
                assembled.append(content)
                used_chars += content_chars

        final_prompt = "\n\n---\n\n".join(assembled)
        logger.info(
            "上下文组装完成: %d chars / %s tokens (限额=%d tokens)",
            len(final_prompt),
            len(final_prompt) // _ESTIMATED_CHARS_PER_TOKEN,
            self.max_context_tokens,
        )
        return final_prompt

    # ================================================================
    # Layer Builders — 各层的独立构建方法
    # ================================================================

    def _default_system_prompt(self) -> str:
        """默认系统提示词（无外部注入时使用）"""
        return (
            "你是一个浏览器自动化 Agent，运行在 ShadowOS 安全沙箱中。\n"
            "你通过调用 Tool 来操作浏览器、读写文件、执行命令。\n"
            "每次执行一个 Tool 调用，等待观察结果后再决定下一步。\n"
            "安全规则：不得绕过安全审查、不得尝试访问系统敏感文件、不得尝试越权。"
        )

    def _build_task_layer(self, state: AgentState) -> str:
        """L2: 任务目标与执行计划"""
        lines = [
            f"## 任务目标\n{state.task_description}",
        ]
        if state.plan_steps:
            lines.append("\n## 执行计划")
            for step in state.plan_steps:
                status = "✓" if step.step_number <= state.current_step_index else "○"
                lines.append(
                    f"{status} Step {step.step_number}: {step.description} "
                    f"[{step.skill_name}]"
                )
        return "\n".join(lines)

    def _build_execution_layer(self, state: AgentState) -> str:
        """L7: 执行进度"""
        return (
            f"## 执行状态\n"
            f"状态: {state.agent_status.value} | "
            f"进度: Step {state.current_step_index}/{len(state.plan_steps)} | "
            f"已执行: {state.total_steps_executed} 步 | "
            f"剩余: {state.steps_remaining} 步"
        )

    def _build_observation_layer(self, state: AgentState) -> str:
        """L4: 结构化观察摘要（增量更新）"""
        parts: list[str] = ["## 观察摘要"]
        if state.observation_summary:
            parts.append(state.observation_summary)
        if state.last_observation:
            obs = state.last_observation
            if obs.summary:
                parts.append(f"\n最近观察: {obs.summary}")
            if obs.page_title:
                parts.append(f"页面标题: {obs.page_title}")
            if obs.page_url:
                parts.append(f"当前 URL: {obs.page_url}")
            if obs.errors:
                parts.append(f"错误: {', '.join(obs.errors)}")
        if len(parts) == 1:
            parts.append("（尚无观察数据）")
        return "\n".join(parts)

    def _build_working_layer(self, state: AgentState) -> str:
        """L3: 最近步骤的详细记录"""
        recent = state.execution_history[-5:]
        if not recent:
            return "## 执行记录\n（尚无执行记录）"

        lines = ["## 最近执行步骤"]
        for step in recent:
            outcome = "✓" if step.success else "✗"
            err = f" 错误: {step.error_message}" if step.error_message else ""
            obs = ""
            if step.observation_structured:
                summary = step.observation_structured.get("summary", "")
                if summary:
                    obs = f" 观察到: {summary}"
            lines.append(
                f"{outcome} Step {step.step_number}: {step.plan_step.description} "
                f"[{step.plan_step.skill_name}] "
                f"({step.execution_time_ms}ms){err}{obs}"
            )
        return "\n".join(lines)

    def _build_memory_layer(self, state: AgentState) -> str:
        """L5: 向量检索的长期记忆"""
        if not state.memory_context:
            return ""
        return f"## 相关历史记忆\n{state.memory_context}"

    def _build_tool_schema_layer(self, selector: SkillSelector) -> str:
        """L6: 当前可用的 Tool 列表（轻量 schema，不加载 markdown）"""
        tools = selector.get_llm_tools()
        if not tools:
            return "## 可用工具\n（无可用工具）"

        lines = ["## 可用工具"]
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name", "unknown")
            desc = func.get("description", "")
            # 只取第一句描述，节省 token
            short_desc = desc.split("。")[0].split(".")[0][:80]
            lines.append(f"- **{name}**: {short_desc}")
        return "\n".join(lines)

    def _build_tool_doc_layer(
        self,
        selector: SkillSelector,
        skill_doc_name: Optional[str] = None,
    ) -> str:
        """
        Tool Doc: 按需加载的 skill.md 完整文档

        只在 Agent 明确选中某个 Skill 后加载该 Skill 的 skill.md。
        这是两层信息模型的核心：schema 轻量前置 + skill.md 按需加载。
        """
        if not skill_doc_name:
            return ""
        doc = selector.get_skill_doc(skill_doc_name)
        if not doc:
            return ""
        return f"## Skill 详细文档: {skill_doc_name}\n{doc}"

    # ================================================================
    # Token 估算
    # ================================================================

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """粗略估算文本的 token 数（1 token ≈ 4 英文字符 / 1 中文字符）"""
        if not text:
            return 0
        # 中文字符 ≈ 1 token，英文 ≈ 4 chars / token
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return chinese_chars + other_chars // 4
