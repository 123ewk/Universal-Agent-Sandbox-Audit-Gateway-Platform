"""
Agent 状态模型 — LangGraph 共享状态定义

设计动机：
  LangGraph 的 StateGraph 需要单一 Pydantic BaseModel 作为各节点共享的状态容器。
  所有节点（Plan/Execute/Observe/Reflect）读写同一个 AgentState 实例，
  LangGraph 自动处理状态持久化和检查点。

核心字段分层：
  task:       任务标识与描述
  plan:       LLM 生成的执行计划
  execution:  运行时计数器与状态
  working:    最近 N 步的详细记录（注入 LLM 上下文）
  observation: 结构化观察摘要（增量更新）
  memory:     向量检索引用列表
  cost:       费用追踪

使用方式：
  from app.agent.state import AgentState, StepRecord, AgentStatus
  graph = StateGraph(AgentState)
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator


# ====================================================================
# AgentStatus — 执行状态枚举
# ====================================================================


class AgentStatus(str, Enum):
    """Agent 执行状态"""
    IDLE = "idle"              # 初始状态，等待开始
    ANALYZING = "analyzing"    # 正在分析意图（Intent Analyzer）
    PLANNING = "planning"      # LLM 正在拆解任务
    EXECUTING = "executing"    # 正在执行步骤
    OBSERVING = "observing"    # 正在处理观察结果
    REFLECTING = "reflecting"  # LLM 正在评估执行结果
    WAITING_APPROVAL = "waiting_approval"  # 等待人类审批
    WAITING_USER = "waiting_user"          # 等待人类回答提问（歧义澄清）
    COMPLETED = "completed"    # 任务执行成功
    FAILED = "failed"          # 任务执行失败
    CANCELLED = "cancelled"    # 用户手动取消


# ====================================================================
# PlanStep — 单个执行步骤
# ====================================================================


class PlanStep(BaseModel):
    """
    LLM Planner 生成的单个执行步骤

    示例：
      PlanStep(
          step_number=1,
          description="导航到百度首页",
          skill_name="browser_goto",
          skill_params={"url": "https://www.baidu.com"},
          expected_outcome="页面加载完成，显示搜索框",
      )
    """
    step_number: int = Field(..., ge=1, description="步骤序号（从 1 开始）")
    description: str = Field(..., min_length=1, description="本步骤的自然语言描述")
    skill_name: str = Field(..., min_length=1, description="执行的 Skill 名称")
    skill_params: dict[str, Any] = Field(default_factory=dict, description="Skill 参数")
    expected_outcome: str = Field(default="", description="预期结果描述")

    # 思考过程（Agent 透明化核心字段）
    thought: str = Field(default="", description="Agent 在本步骤前的思考过程（自然语言）")
    reasoning_chain: list[str] = Field(default_factory=list, description="推理链路，每步一个字符串")

    # 检查点：该步骤是否需要人类审批才能继续
    requires_approval: bool = Field(default=False, description="是否需要人类审批")
    # 该步骤解锁的 Tier（如步骤涉及点击则解锁 INTERACTION）
    required_tier: Optional[str] = Field(default=None, description="执行此步骤需要解锁的 Tier")


# ====================================================================
# IntentResult — 意图分析结果
# ====================================================================


class IntentResult(BaseModel):
    """
    Intent Analyzer 节点输出的意图分析结果

    LLM 分析用户任务，输出结构化意图：
      - intent_category: WEB_SEARCH / LOCAL_APP_LOOKUP / FILE_OPERATION / GENERAL_QA / ...
      - confidence: 置信度
      - clarifying_questions: 如果有歧义，Agent 向用户提问
      - suggested_tools: 建议的工具列表（供后续 Plan 使用）
      - reasoning: 推理过程
    """
    intent_category: str = Field(
        default="GENERAL_QA",
        description="意图分类: WEB_SEARCH / LOCAL_APP_LOOKUP / FILE_OPERATION / SYSTEM_INFO / GENERAL_QA",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="意图置信度")
    clarifying_questions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="需要用户澄清的问题 [{question, options}]",
    )
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="建议使用的工具名称列表",
    )
    reasoning: str = Field(default="", description="意图推理过程（自然语言）")
    reasoning_chain: list[str] = Field(
        default_factory=list,
        description="推理链路，每步一个字符串",
    )

    @property
    def has_questions(self) -> bool:
        """是否需要用户澄清"""
        return len(self.clarifying_questions) > 0


# ====================================================================
# ActionProposal — Agent 执行提案
# ====================================================================


class ActionProposal(BaseModel):
    """
    Agent 在执行前输出的 Action Proposal

    与 PlanStep 不同——PlanStep 是执行计划中的步骤定义，
    ActionProposal 是每一步执行前 Agent 输出的"思考 + 行动方案"。

    Agent 不直接调 Skill，而是输出 ActionProposal，
    由 Runtime 审查（RiskEngine + AuditGateway）后再执行。
    """
    thought: str = Field(default="", description="Agent 当前在想什么（自然语言）")
    intent: str = Field(default="", description="识别到的子意图")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="本步置信度")
    proposed_action: str = Field(default="", description="建议调用的 Skill 名称")
    action_params: dict[str, Any] = Field(default_factory=dict, description="Skill 参数")
    expected_outcome: str = Field(default="", description="预期执行结果")
    alternative_actions: list[str] = Field(default_factory=list, description="备选方案（Skill 名称列表）")
    requires_permission: bool = Field(default=False, description="是否认为需要人类授权")
    reasoning_chain: list[str] = Field(default_factory=list, description="推理链路，每步一个字符串")


# ====================================================================
# LLMUsage — LLM 调用指标
# ====================================================================


@dataclass
class LLMUsage:
    """单次 LLM 调用的完整指标"""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    estimated_cost: Decimal = Decimal("0")


# ====================================================================
# StepRecord — 已执行步骤的完整记录
# ====================================================================


class StepRecord(BaseModel):
    """
    每一步执行后的完整记录

    包含：做了什么 → 结果怎样 → 观察到了什么 → 花了多久 → 花费多少
    同时保存风险评估结果用于审计。
    """
    step_number: int = Field(..., ge=1)
    plan_step: PlanStep = Field(..., description="原始计划步骤")

    # 执行结果
    success: bool = Field(default=False)
    result_data: Any = Field(default=None, description="SkillResult.data")
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")

    # 时间与成本
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    execution_time_ms: int = Field(default=0, description="执行耗时（毫秒）")
    llm_cost: Decimal = Field(default=Decimal("0"), description="本步骤 LLM 调用费用")
    tokens_used: int = Field(default=0, description="本步骤 Token 消耗")
    llm_usage: Optional[LLMUsage] = Field(default=None, description="LLM 调用完整指标")

    # 观察（执行后从页面/环境捕获的结构化信息）
    observation_raw: Optional[str] = Field(default=None, description="原始观察数据摘要")
    observation_structured: Optional[dict[str, Any]] = Field(
        default=None,
        description="结构化观察（UI 元素、文本、错误等）",
    )

    # 安全审计
    risk_level: Optional[int] = Field(default=None, description="RiskLevel 数值 (1-5)")
    risk_score: Optional[int] = Field(default=None, description="风险评分 (0-100)")
    required_approval: bool = Field(default=False)
    approval_granted: Optional[bool] = Field(default=None, description="审批是否已通过")

    @property
    def duration_seconds(self) -> float:
        return self.execution_time_ms / 1000.0

    @property
    def is_completed(self) -> bool:
        return self.finished_at is not None


# ====================================================================
# ObservationRecord — 结构化观察
# ====================================================================


class ObservationRecord(BaseModel):
    """
    经过 ObservationPipeline 处理后的结构化观察

    源数据（原始 HTML/DOM/日志）不进 Prompt，
    经过 NoiseFilter → UIParser → Summarizer 后产生此结构。
    """
    summary: str = Field(default="", description="页面内容的一句话摘要")
    page_title: Optional[str] = Field(default=None, description="页面标题")
    page_url: Optional[str] = Field(default=None, description="当前页面 URL")

    # UI 元素清单（仅交互元素）
    interactive_elements: list[dict[str, str]] = Field(
        default_factory=list,
        description="可交互元素列表 [{type, text, selector}]",
    )

    # 错误/异常信息（如果有）
    errors: list[str] = Field(default_factory=list, description="页面上的错误信息")
    warnings: list[str] = Field(default_factory=list, description="警告信息")

    # 原始数据引用（不存内容，只存引用路径）
    raw_data_ref: Optional[str] = Field(
        default=None,
        description="原始观察数据的存储引用（文件路径或 Redis key）",
    )


# ====================================================================
# AgentState — LangGraph 全局状态
# ====================================================================


class AgentState(BaseModel):
    """
    LangGraph StateGraph 的共享状态容器

    所有 Agent 节点通过此模型共享数据。
    使用 Pydantic BaseModel（langgraph 原生支持），
    而非 dataclass（langgraph 对 Pydantic 支持更好：自动序列化/验证）。

    字段分组：
      task:       任务的"身份证"
      plan:       LLM 规划结果
      execution:  运行时追踪
      working:    最近步骤的详细记录（Working Context 数据源）
      observation: 增量更新的结构化观察摘要
      memory:     向量检索引用
      cost:       费用汇总
    """

    # ---- Task ----
    session_id: int = Field(default=0, description="数据库 AgentSession.id")
    task_description: str = Field(default="", description="用户原始任务描述")
    agent_status: AgentStatus = Field(default=AgentStatus.IDLE)

    # ---- Intent ----
    intent_result: Optional[IntentResult] = Field(
        default=None,
        description="Intent Analyzer 的分析结果",
    )
    # 当 WAITING_USER 时，Agent 向用户提出的问题
    current_question: Optional[dict[str, Any]] = Field(
        default=None,
        description="Agent 当前向用户提出的问题",
    )

    # ---- Plan ----
    plan_steps: list[PlanStep] = Field(default_factory=list, description="LLM 生成的执行计划")
    current_step_index: int = Field(default=0, description="当前执行到第几步（0-indexed）")

    # ---- Execution ----
    execution_history: list[StepRecord] = Field(
        default_factory=list,
        description="所有已执行步骤的完整记录",
    )
    total_steps_planned: int = Field(default=0)
    total_steps_executed: int = Field(default=0)
    max_steps: int = Field(default=50, description="单次会话最大执行步数（硬限制）")

    # ---- Working Context ----
    # 注：Working Context 由 ContextManager 从 execution_history[-5:] 动态构建，
    #      不单独存储此字段。ContextManager 组装时实时生成。
    #      这里只保留最近一次构建的缓存，避免重复计算。
    # PrivateAttr 不会序列化，不参与 LangGraph 状态持久化
    _working_context_cache: Optional[str] = PrivateAttr(default=None)

    # ---- Observation ----
    observation_summary: str = Field(
        default="",
        description="增量更新的执行摘要（每次 Observe 后追加）",
    )
    last_observation: Optional[ObservationRecord] = Field(
        default=None,
        description="最近一次的观察记录",
    )

    # ---- Memory ----
    # 向量检索返回的相关记忆 ID 列表（具体内容由 ContextManager 从 DB 获取）
    relevant_memory_ids: list[int] = Field(default_factory=list)
    memory_context: str = Field(default="", description="从向量记忆检索的内容文本（注入 LLM）")

    # ---- Cost ----
    total_llm_cost: Decimal = Field(default=Decimal("0"), description="累计 LLM 费用（USD）")
    total_tokens_used: int = Field(default=0, description="累计 Token 消耗")

    # ---- Error ----
    error_message: Optional[str] = Field(default=None, description="致命错误信息（导致 Agent 停止）")
    cancellation_reason: Optional[str] = Field(default=None, description="取消原因")

    # ---- Routing ----
    # LangGraph 条件路由标记，由节点设置，由路由函数消费后重置
    needs_replan: bool = Field(default=False, description="Reflect 决定重新规划时设置为 True")

    # ================================================================
    # 便捷属性
    # ================================================================

    @property
    def current_plan_step(self) -> Optional[PlanStep]:
        """当前正在执行的计划步骤"""
        if 0 <= self.current_step_index < len(self.plan_steps):
            return self.plan_steps[self.current_step_index]
        return None

    @property
    def is_finished(self) -> bool:
        """Agent 是否已完成（成功/失败/取消）"""
        return self.agent_status in (
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        )

    @property
    def recent_steps(self) -> list[StepRecord]:
        """最近 5 步的执行记录"""
        return self.execution_history[-5:]

    @property
    def last_step(self) -> Optional[StepRecord]:
        """上一步的执行记录"""
        if self.execution_history:
            return self.execution_history[-1]
        return None

    @property
    def steps_remaining(self) -> int:
        """剩余计划步数"""
        return max(0, len(self.plan_steps) - self.current_step_index)

    @property
    def has_any_success(self) -> bool:
        """是否至少有一个步骤执行成功"""
        return any(s.success for s in self.execution_history)

    @property
    def progress_pct(self) -> float:
        """执行进度百分比"""
        if not self.plan_steps:
            return 0.0
        return min(100.0, (self.current_step_index / len(self.plan_steps)) * 100)

    # ================================================================
    # 状态转换方法
    # ================================================================

    def transition_to(self, status: AgentStatus) -> None:
        """状态转换（仅用于日志追踪，不影响 LangGraph 状态机）"""
        valid_transitions = {
            AgentStatus.IDLE: {AgentStatus.ANALYZING, AgentStatus.PLANNING, AgentStatus.FAILED},
            AgentStatus.ANALYZING: {AgentStatus.PLANNING, AgentStatus.WAITING_USER, AgentStatus.FAILED},
            AgentStatus.PLANNING: {AgentStatus.EXECUTING, AgentStatus.FAILED},
            AgentStatus.EXECUTING: {AgentStatus.OBSERVING, AgentStatus.WAITING_APPROVAL, AgentStatus.WAITING_USER, AgentStatus.FAILED},
            AgentStatus.OBSERVING: {AgentStatus.REFLECTING, AgentStatus.EXECUTING, AgentStatus.PLANNING},
            AgentStatus.REFLECTING: {AgentStatus.EXECUTING, AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.PLANNING},
            AgentStatus.WAITING_APPROVAL: {AgentStatus.EXECUTING, AgentStatus.CANCELLED},
            AgentStatus.WAITING_USER: {AgentStatus.EXECUTING, AgentStatus.PLANNING, AgentStatus.CANCELLED},
        }
        allowed = valid_transitions.get(self.agent_status, set())
        if status not in allowed and self.agent_status != status:
            raise ValueError(
                f"非法状态转换: {self.agent_status.value} → {status.value}. "
                f"允许: {[s.value for s in allowed]}"
            )
        self.agent_status = status

    def record_step(self, record: StepRecord) -> None:
        """记录一个已执行的步骤"""
        self.execution_history.append(record)
        self.total_steps_executed = len(self.execution_history)
        self.total_llm_cost += record.llm_cost
        self.total_tokens_used += record.tokens_used

    def add_cost(self, cost: Decimal, tokens: int = 0) -> None:
        """累加 LLM 费用和 Token 消耗"""
        self.total_llm_cost += cost
        self.total_tokens_used += tokens

    @field_validator("max_steps")
    @classmethod
    def max_steps_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_steps 必须 >= 1")
        if v > 200:
            raise ValueError("max_steps 最多 200 步（安全硬限制）")
        return v
