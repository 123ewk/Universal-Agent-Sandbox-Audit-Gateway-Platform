"""
WebSocket 事件协议 — 统一消息格式

设计动机：
  前端和 Agent 之间通过 WebSocket 通信，需要统一的消息格式。
  使用 Pydantic 模型确保类型安全 + 自动 JSON 序列化。

命名空间：
  agent.*     — Agent 生命周期事件（planning/executing/completed/failed）
  sandbox.*   — 沙箱页面事件（navigation/screenshot/element）
  audit.*     — 审计事件（risk/alert/log）
  approval.*  — 审批事件（required/approved/denied/timeout）

消息格式：
  {
    "event": "agent.step.completed",
    "session_id": 42,
    "timestamp": "2026-05-28T12:00:00Z",
    "payload": { ... }
  }

使用方式：
  msg = AgentStepCompleted(session_id=42, payload={...})
  ws.send_json(msg.to_message())
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ====================================================================
# EventType — 所有事件类型枚举
# ====================================================================


class EventType(str, Enum):
    """WebSocket 事件类型（命名空间分层）"""

    # --- agent.* ---
    AGENT_STARTED = "agent.started"
    AGENT_PLANNING = "agent.planning"
    AGENT_PLAN_COMPLETED = "agent.plan.completed"
    AGENT_STEP_STARTED = "agent.step.started"
    AGENT_STEP_COMPLETED = "agent.step.completed"
    AGENT_STEP_FAILED = "agent.step.failed"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    AGENT_CANCELLED = "agent.cancelled"

    # --- sandbox.* ---
    SANDBOX_NAVIGATION = "sandbox.navigation"
    SANDBOX_SCREENSHOT = "sandbox.screenshot"
    SANDBOX_ELEMENT_INTERACTION = "sandbox.element.interaction"
    SANDBOX_PAGE_INFO = "sandbox.page.info"

    # --- audit.* ---
    AUDIT_RISK_DETECTED = "audit.risk.detected"
    AUDIT_ALERT = "audit.alert"
    AUDIT_LOG_CREATED = "audit.log.created"

    # --- approval.* ---
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_DENIED = "approval.denied"
    APPROVAL_TIMEOUT = "approval.timeout"

    # --- system.* ---
    SYSTEM_HEARTBEAT = "system.heartbeat"
    SYSTEM_CONNECTED = "system.connected"
    SYSTEM_DISCONNECTED = "system.disconnected"
    SYSTEM_ERROR = "system.error"


# ====================================================================
# 事件 Payload 模型
# ====================================================================


class StepPayload(BaseModel):
    """步骤事件 Payload"""
    step_number: int
    skill_name: str
    description: str = ""
    success: bool = True
    execution_time_ms: int = 0
    error: Optional[str] = None
    result_data: Optional[dict[str, Any]] = None


class NavigationPayload(BaseModel):
    """导航事件 Payload"""
    from_url: str = ""
    to_url: str
    title: str = ""
    status_code: Optional[int] = None


class ScreenshotPayload(BaseModel):
    """截图事件 Payload"""
    path: str
    filename: str
    size_bytes: int = 0
    step_number: int = 0


class RiskPayload(BaseModel):
    """风险检测 Payload"""
    risk_score: int
    risk_level: int
    reasons: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class ApprovalPayload(BaseModel):
    """审批事件 Payload"""
    approval_id: int
    skill_name: str
    risk_score: int = 0
    risk_reasons: list[str] = Field(default_factory=list)
    step_number: int = 0


class PageInfoPayload(BaseModel):
    """页面信息 Payload"""
    url: str = ""
    title: str = ""
    element_count: int = 0
    screenshot_path: Optional[str] = None


class CostPayload(BaseModel):
    """费用信息 Payload"""
    llm_cost: str = "0"
    tokens_used: int = 0
    total_steps: int = 0


# ====================================================================
# 统一消息
# ====================================================================


class WSMessage(BaseModel):
    """
    WebSocket 统一消息格式

    前端通过 event 字段路由到不同的 UI 组件：
      agent.*     → AgentPanel
      sandbox.*   → BrowserView
      audit.*     → AlertBadge
      approval.*  → ApprovalDialog
    """
    event: str
    session_id: int
    seq: Optional[int] = None      # 事件序号，每个 session 独立递增，前端用于去重和顺序恢复
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ====================================================================
# 事件工厂函数
# ====================================================================


def make_message(
    event: EventType,
    session_id: int,
    payload: BaseModel | dict[str, Any],
) -> WSMessage:
    """创建 WSMessage 的快捷函数"""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    return WSMessage(event=event.value, session_id=session_id, payload=payload)


def heartbeat(session_id: int) -> WSMessage:
    return WSMessage(event=EventType.SYSTEM_HEARTBEAT.value, session_id=session_id)


def connected(session_id: int) -> WSMessage:
    return WSMessage(event=EventType.SYSTEM_CONNECTED.value, session_id=session_id)


def agent_step_completed(session_id: int, payload: StepPayload) -> WSMessage:
    return make_message(EventType.AGENT_STEP_COMPLETED, session_id, payload)


def agent_step_failed(session_id: int, payload: StepPayload) -> WSMessage:
    return make_message(EventType.AGENT_STEP_FAILED, session_id, payload)


def approval_required(session_id: int, payload: ApprovalPayload) -> WSMessage:
    return make_message(EventType.APPROVAL_REQUIRED, session_id, payload)


def audit_risk_detected(session_id: int, payload: RiskPayload) -> WSMessage:
    return make_message(EventType.AUDIT_RISK_DETECTED, session_id, payload)


def sandbox_screenshot(session_id: int, payload: ScreenshotPayload) -> WSMessage:
    return make_message(EventType.SANDBOX_SCREENSHOT, session_id, payload)


def sandbox_navigation(session_id: int, payload: NavigationPayload) -> WSMessage:
    return make_message(EventType.SANDBOX_NAVIGATION, session_id, payload)
