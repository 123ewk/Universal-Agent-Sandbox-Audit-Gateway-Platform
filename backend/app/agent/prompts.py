"""
Prompt 模板 — System/Plan/Reflect Prompt 工厂

设计动机：
  不同 LLM 模型对 Prompt 格式要求不同（OpenAI 用 system/user/assistant 角色，
  DeepSeek 类似 OpenAI 格式）。Prompt 模板需要：
    1. 支持模型定制（不同模型有不同的用语偏好）
    2. 支持变量注入（{task}, {tools}, {working_context} 等）
    3. 结构化输出指令（Plan 生成 JSON，Reflect 生成结构化评估）

模板风格：
  遵循 ShadowOS "系统决定什么进上下文" 原则，
  SystemPrompt 精炼扼要（~300 tokens），不堆砌冗长规则。
  browser-use 的 300 行 system prompt 做法已证明会导致规则被 LLM 忽略。

使用方式：
  from app.agent.prompts import PromptBuilder
  builder = PromptBuilder(provider="deepseek")
  system = builder.build_system(selector)
  plan = builder.build_plan_prompt(task, state, selector)
"""
import json
from typing import Optional

from app.agent.state import AgentState
from app.skills.selector import SkillSelector


class PromptBuilder:
    """
    Prompt 模板工厂

    按 LLM 提供商定制模板风格：
      openai:   Markdown 格式，强调角色扮演
      deepseek: 简洁中文，强调执行力
      claude:   英文，强调安全边界
    """

    def __init__(self, provider: str = "deepseek") -> None:
        self.provider = provider.lower()

    # ================================================================
    # System Prompt（Layer 1 固定上下文）
    # ================================================================

    def build_system(self) -> str:
        """构建 System Prompt"""
        return _SYSTEM_PROMPTS.get(self.provider, _SYSTEM_PROMPTS["deepseek"])

    # ================================================================
    # Intent Prompt — LLM 分析用户意图
    # ================================================================

    def build_intent_prompt(self, task_description: str) -> str:
        """
        构建 Intent 分析阶段的 Prompt

        要求 LLM 先分析用户意图，输出结构化意图结果，
        识别是否需要向用户提问澄清歧义。
        """
        return _INTENT_TEMPLATES.get(self.provider, _INTENT_TEMPLATES["deepseek"]).format(
            task=task_description,
        )

    # ================================================================
    # Plan Prompt — LLM 拆解任务为步骤（含 thought/reasoning）
    # ================================================================

    def build_plan_prompt(
        self,
        task_description: str,
        state: AgentState,
        selector: SkillSelector,
    ) -> str:
        """
        构建 Plan 阶段的 Prompt

        要求 LLM 将用户任务拆解为具体的 Skill 调用步骤，
        每个步骤必须包含 thought/reasoning 字段。
        """
        tools_desc = self._format_tools_for_plan(selector)
        history_context = self._format_history_for_plan(state)
        intent_context = ""
        if state.intent_result:
            intent = state.intent_result
            intent_context = (
                f"\n## 意图分析结果\n"
                f"意图: {intent.intent_category} (置信度: {intent.confidence:.0%})\n"
                f"建议工具: {', '.join(intent.suggested_tools) if intent.suggested_tools else '无'}\n"
                f"推理: {intent.reasoning}\n"
            )

        return _PLAN_TEMPLATES[self.provider].format(
            task=task_description,
            tools=tools_desc,
            history=history_context,
            intent=intent_context,
        )

    # ================================================================
    # Execution Prompt — 当前步骤的详细执行指令
    # ================================================================

    def build_execute_prompt(
        self,
        state: AgentState,
        skill_doc: Optional[str] = None,
    ) -> str:
        """
        构建 Execute 阶段 Prompt

        给 LLM 提供当前步骤的上下文 + 选中 Skill 的完整文档，
        LLM 输出 function call（tool_choice）。
        """
        step = state.current_plan_step
        if step is None:
            return "没有可执行的步骤。"

        parts = [
            f"当前任务: {state.task_description}",
            f"当前步骤 ({state.current_step_index + 1}/{len(state.plan_steps)}): {step.description}",
            f"应调用的 Skill: {step.skill_name}",
            f"参数: {json.dumps(step.skill_params, ensure_ascii=False)}",
        ]

        if state.last_observation:
            obs = state.last_observation
            parts.append(f"\n上一观察: {obs.summary}")
            if obs.errors:
                parts.append(f"上一错误: {', '.join(obs.errors)}")

        if skill_doc:
            parts.append(f"\n{skill_doc}")

        return "\n".join(parts)

    # ================================================================
    # Reflect Prompt — LLM 评估执行结果
    # ================================================================

    def build_reflect_prompt(self, state: AgentState) -> str:
        """
        构建 Reflect 阶段的 Prompt

        要求 LLM 评估最近一步的执行结果，决定：
          continue: 继续执行下一步
          retry:    重试当前步骤
          replan:   重新规划（当前计划不可行）
          complete: 任务已完成
          abort:    无法继续，需要人类介入
        """
        last_step = state.last_step
        if last_step is None:
            return "没有需要评估的步骤。"

        step_info = (
            f"步骤 {last_step.step_number}: {last_step.plan_step.description}\n"
            f"Skill: {last_step.plan_step.skill_name}\n"
            f"结果: {'成功' if last_step.success else '失败'}\n"
        )
        if last_step.error_message:
            step_info += f"错误: {last_step.error_message}\n"
        if last_step.observation_structured:
            obs_summary = last_step.observation_structured.get("summary", "")
            if obs_summary:
                step_info += f"观察到: {obs_summary}\n"

        remaining_steps = state.plan_steps[state.current_step_index + 1:]
        remaining_desc = "\n".join(
            f"  Step {s.step_number}: {s.description}" for s in remaining_steps
        ) if remaining_steps else "（无）"

        return _REFLECT_TEMPLATES[self.provider].format(
            step_info=step_info,
            task=state.task_description,
            progress=f"已完成 {state.current_step_index}/{len(state.plan_steps)} 步",
            remaining_steps=remaining_desc,
        )

    # ================================================================
    # 内部格式化方法
    # ================================================================

    def _format_tools_for_plan(self, selector: SkillSelector) -> str:
        """格式化可用工具列表供 Plan 使用"""
        tools = selector.get_llm_tools()
        lines: list[str] = []
        for tool in tools:
            func = tool.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _format_history_for_plan(self, state: AgentState) -> str:
        """格式化历史步骤供 Plan 使用"""
        if not state.execution_history:
            return "（首次执行，无历史）"
        lines = ["最近的执行步骤:"]
        for step in state.execution_history[-3:]:
            outcome = "OK" if step.success else "ERR"
            lines.append(
                f"  Step {step.step_number}: {step.plan_step.skill_name} → {outcome}"
            )
        return "\n".join(lines)


# ====================================================================
# System Prompt 模板（按模型提供商定制）
# ====================================================================

_SYSTEM_PROMPTS: dict[str, str] = {
    "deepseek": (
        "你是一个浏览器自动化 Agent，运行在安全沙箱 ShadowOS 中。\n"
        "你的能力：通过调用 Tool 操作浏览器、读写文件、执行命令。\n"
        "你的约束：\n"
        "1. 每次只执行一个 Tool 调用，等待观察结果后再继续\n"
        "2. 不得尝试访问系统敏感路径（/etc/、/root/、.ssh/）\n"
        "3. 遇到审批要求时等待人类确认，不得自行绕过\n"
        "4. 原始 HTML/日志不进入上下文，你会收到结构化摘要\n"
        "5. 如果步骤连续失败 3 次，应主动要求 replan"
    ),
    "openai": (
        "You are a browser automation agent operating within the ShadowOS security sandbox.\n"
        "Your capabilities: navigate browsers, click elements, type text, extract content, "
        "read/write files, and execute shell commands via Tool calls.\n"
        "Constraints:\n"
        "1. Execute one Tool call at a time; wait for observation before proceeding\n"
        "2. Never access sensitive system paths (/etc/, /root/, .ssh/)\n"
        "3. Wait for human approval when required; never bypass security\n"
        "4. Raw HTML/logs are pre-processed; you receive structured summaries\n"
        "5. If a step fails 3 times consecutively, request a replan"
    ),
    "claude": (
        "You are a browser automation agent running in ShadowOS, a security sandbox.\n"
        "Capabilities: browser navigation, element interaction, content extraction, "
        "file I/O, and shell execution via Tool calls.\n"
        "Security boundaries:\n"
        "1. One Tool call per turn; await structured observation before next action\n"
        "2. System paths (/etc/, /root/, .ssh/) are off-limits — do not attempt access\n"
        "3. Approval gates are mandatory — do not reason about bypassing them\n"
        "4. You receive structured observations, not raw HTML — work with what you get\n"
        "5. Three consecutive failures on a step → flag for replan, do not loop blindly"
    ),
}


# ====================================================================
# Intent Prompt 模板
# ====================================================================

_INTENT_TEMPLATES: dict[str, str] = {
    "deepseek": (
        "你是一个任务意图分析器。分析用户的任务，输出结构化的意图分类结果。\n\n"
        "## 用户任务\n{task}\n\n"
        "## 意图分类\n"
        "从以下类别中选择最匹配的：\n"
        "- WEB_SEARCH: 需要浏览器搜索/访问网页\n"
        "- LOCAL_APP_LOOKUP: 查找本地安装的应用/文件\n"
        "- FILE_OPERATION: 文件读写/移动/删除操作\n"
        "- SYSTEM_INFO: 获取系统信息\n"
        "- GENERAL_QA: 通用问答/无需工具\n\n"
        "## 要求\n"
        "1. 如果任务有歧义（如可能指本地或网页），必须生成 clarifying_questions\n"
        "2. 如果任务明确，closing_questions 留空\n"
        "3. suggested_tools 列出你可能需要用到的工具\n"
        "4. reasoning_chain 列出推理步骤\n\n"
        "## 输出格式\n"
        "只输出 JSON：\n"
        "{{\n"
        '  "intent_category": "WEB_SEARCH",\n'
        '  "confidence": 0.9,\n'
        '  "clarifying_questions": [{{"question": "你指的是本地飞书还是网页飞书？", "options": ["本地应用", "网页版"]}}],\n'
        '  "suggested_tools": ["browser_goto", "desktop_search"],\n'
        '  "reasoning": "用户提到飞书，可能是本地应用或网页版",\n'
        '  "reasoning_chain": ["分析任务关键词: 飞书", "判断可能意图: 本地或网页", "建议两个方向"]\n'
        "}}\n"
        "只输出 JSON，不要输出其他内容。"
    ),
    "openai": (
        "You are a task intent analyzer. Analyze the user's task and output structured intent.\n\n"
        "## Task\n{task}\n\n"
        "## Intent categories\n"
        "- WEB_SEARCH: Browser search / web access needed\n"
        "- LOCAL_APP_LOOKUP: Look up locally installed apps/files\n"
        "- FILE_OPERATION: File read/write/move/delete\n"
        "- SYSTEM_INFO: System information query\n"
        "- GENERAL_QA: General Q&A, no tools needed\n\n"
        "## Rules\n"
        "1. If the task is ambiguous, generate clarifying_questions\n"
        "2. If clear, leave clarifying_questions empty\n"
        "3. Suggest tools in suggested_tools\n"
        "4. List reasoning steps in reasoning_chain\n\n"
        "## Output (JSON only)\n"
        "{{\n"
        '  "intent_category": "WEB_SEARCH",\n'
        '  "confidence": 0.9,\n'
        '  "clarifying_questions": [],\n'
        '  "suggested_tools": ["browser_goto"],\n'
        '  "reasoning": "...",\n'
        '  "reasoning_chain": ["..."]\n'
        "}}"
    ),
    "claude": (
        "You are a task intent analyzer. Analyze the user's task and output structured intent.\n\n"
        "## Task\n{task}\n\n"
        "## Intent categories\n"
        "- WEB_SEARCH: Browser search / web access\n"
        "- LOCAL_APP_LOOKUP: Local app/file lookup\n"
        "- FILE_OPERATION: File operations\n"
        "- SYSTEM_INFO: System info\n"
        "- GENERAL_QA: No tools needed\n\n"
        "## Rules\n"
        "1. Ambiguous tasks → generate clarifying_questions\n"
        "2. Clear tasks → leave clarifying_questions empty\n"
        "3. Include suggested_tools and reasoning_chain\n\n"
        "## Output (JSON only)\n"
        "{{\n"
        '  "intent_category": "WEB_SEARCH",\n'
        '  "confidence": 0.9,\n'
        '  "clarifying_questions": [],\n'
        '  "suggested_tools": ["browser_goto"],\n'
        '  "reasoning": "...",\n'
        '  "reasoning_chain": ["..."]\n'
        "}}"
    ),
}


# ====================================================================
# Plan Prompt 模板
# ====================================================================

_PLAN_TEMPLATES: dict[str, str] = {
    "deepseek": (
        "你是一个任务规划器。请将用户的任务分解为具体的执行步骤，"
        "每步都要展示你的思考过程。\n\n"
        "## 用户任务\n{task}\n\n"
        "## 可用工具\n{tools}\n\n"
        "## 历史上下文\n{history}\n"
        "{intent}\n"
        "## 规划规则\n"
        "1. 每个步骤必须使用一个具体的 Tool\n"
        "2. 步骤之间的数据依赖要明确（前一步的输出可能是后一步的输入）\n"
        "3. 按 Tool 的 Tier 分级规划：先 CORE（导航/截图），再 INTERACTION（点击/输入），"
        "需要时再 FILE/SHELL\n"
        "4. 预计总步数不要超过 10 步\n"
        "5. 每步必须包含 thought（你在想什么）和 reasoning_chain（推理链路）\n\n"
        "## 输出格式\n"
        "请以 JSON 数组格式输出执行计划：\n"
        '[\n'
        '  {{\n'
        '    "step_number": 1,\n'
        '    "description": "步骤描述",\n'
        '    "skill_name": "browser_goto",\n'
        '    "skill_params": {{"url": "https://..."}},\n'
        '    "expected_outcome": "预期结果",\n'
        '    "required_tier": "CORE",\n'
        '    "thought": "我认为首先需要...",\n'
        '    "reasoning_chain": ["分析: 需要访问网页", "决策: 使用 browser_goto", "理由: 导航到目标网站"]\n'
        '  }}\n'
        ']\n\n'
        "只输出 JSON 数组，不要输出其他内容。"
    ),
    "openai": (
        "You are a task planner. Decompose the user's task into concrete execution steps.\n\n"
        "## Task\n{task}\n\n"
        "## Available Tools\n{tools}\n\n"
        "## History\n{history}\n"
        "{intent}\n"
        "## Rules\n"
        "1. Each step must use exactly one Tool\n"
        "2. Plan in Tier order: CORE (navigate/screenshot) → INTERACTION (click/type) "
        "→ FILE/SHELL only when needed\n"
        "3. Maximum 10 steps\n"
        "4. Every step must include thought and reasoning_chain\n\n"
        "## Output\n"
        "Output a JSON array of steps:\n"
        '[\n'
        '  {{\n'
        '    "step_number": 1,\n'
        '    "description": "...",\n'
        '    "skill_name": "browser_goto",\n'
        '    "skill_params": {{"url": "https://..."}},\n'
        '    "expected_outcome": "...",\n'
        '    "required_tier": "CORE",\n'
        '    "thought": "...",\n'
        '    "reasoning_chain": ["..."]\n'
        '  }}\n'
        ']\n'
        "Output only the JSON array, nothing else."
    ),
    "claude": (
        "You are a task planner. Decompose the user's task into concrete execution steps.\n\n"
        "## Task\n{task}\n\n"
        "## Available Tools\n{tools}\n\n"
        "## History\n{history}\n"
        "{intent}\n"
        "## Rules\n"
        "1. Each step uses exactly one Tool\n"
        "2. Tier order: CORE → INTERACTION → FILE/SHELL (only when necessary)\n"
        "3. Maximum 10 steps\n"
        "4. Every step must include thought and reasoning_chain\n\n"
        "## Output format\n"
        "JSON array only:\n"
        '[\n'
        '  {{\n'
        '    "step_number": 1,\n'
        '    "description": "...",\n'
        '    "skill_name": "browser_goto",\n'
        '    "skill_params": {{"url": "https://..."}},\n'
        '    "expected_outcome": "...",\n'
        '    "required_tier": "CORE",\n'
        '    "thought": "...",\n'
        '    "reasoning_chain": ["..."]\n'
        '  }}\n'
        ']\n'
        "Output only the JSON array."
    ),
}


# ====================================================================
# Reflect Prompt 模板
# ====================================================================

_REFLECT_TEMPLATES: dict[str, str] = {
    "deepseek": (
        "你是一个执行评估器。评估刚才执行的步骤结果，并决定下一步动作。\n\n"
        "## 任务目标\n{task}\n\n"
        "## 执行进度\n{progress}\n\n"
        "## 刚执行的步骤\n{step_info}\n"
        "## 剩余步骤\n{remaining_steps}\n\n"
        "## 评估规则\n"
        "1. 步骤成功 + 还有剩余步骤 → continue\n"
        "2. 步骤成功 + 无剩余步骤 → complete\n"
        "3. 步骤失败 + 可重试 → retry（同一参数重试最多 2 次）\n"
        "4. 步骤失败 + 当前计划不可行 → replan\n"
        "5. 检测到安全风险/无法继续 → abort\n\n"
        "## 输出格式\n"
        "请输出 JSON：\n"
        '{{\n'
        '  "decision": "continue|retry|replan|complete|abort",\n'
        '  "reason": "决策理由（一句话）",\n'
        '  "next_action": "下一步做什么（如果是 continue/retry）",\n'
        '  "modified_params": {{}}  // 如果是 retry，这里放修改后的参数\n'
        '}}\n'
        "只输出 JSON，不要输出其他内容。"
    ),
    "openai": (
        "You are an execution evaluator. Assess the last step's result and decide next action.\n\n"
        "## Task\n{task}\n\n"
        "## Progress\n{progress}\n\n"
        "## Last Step\n{step_info}\n"
        "## Remaining Steps\n{remaining_steps}\n\n"
        "## Decision Rules\n"
        "1. Success + remaining steps → continue\n"
        "2. Success + no remaining → complete\n"
        "3. Failure + retryable → retry (max 2 retries)\n"
        "4. Failure + plan invalid → replan\n"
        "5. Safety risk / unrecoverable → abort\n\n"
        "## Output (JSON only)\n"
        '{{\n'
        '  "decision": "continue|retry|replan|complete|abort",\n'
        '  "reason": "...",\n'
        '  "next_action": "...",\n'
        '  "modified_params": {{}}\n'
        '}}'
    ),
    "claude": (
        "You are an execution evaluator. Assess the last step and decide the next action.\n\n"
        "## Task\n{task}\n\n"
        "## Progress\n{progress}\n\n"
        "## Last Step\n{step_info}\n"
        "## Remaining\n{remaining_steps}\n\n"
        "## Rules\n"
        "1. Success + remaining → continue\n"
        "2. Success + done → complete\n"
        "3. Failure + retryable → retry (max 2)\n"
        "4. Failure + bad plan → replan\n"
        "5. Safety / unrecoverable → abort\n\n"
        "## Output (JSON only)\n"
        '{{\n'
        '  "decision": "continue|retry|replan|complete|abort",\n'
        '  "reason": "...",\n'
        '  "next_action": "...",\n'
        '  "modified_params": {{}}\n'
        '}}'
    ),
}
