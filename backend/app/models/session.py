"""
AgentSession 模型

记录每一次 Agent 执行会话的核心信息。
一个 Session 对应一个用户提交的任务，包含从任务接收到执行完成的完整生命周期。
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Text, Enum as SAEnum, Numeric, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModelMixin
import enum


class SessionStatus(str, enum.Enum):
    """
    会话状态枚举

    流程：PENDING → RUNNING → (SUCCESS | FAILED | CANCELLED | APPROVAL_PENDING)
    APPROVAL_PENDING 是 Human-in-the-loop 的核心状态：
    Agent 触发了高危操作，等待人类审批。
    """
    PENDING = "pending"              # 等待执行
    RUNNING = "running"              # 执行中
    SUCCESS = "success"              # 执行成功
    FAILED = "failed"                # 执行失败
    CANCELLED = "cancelled"          # 用户手动取消
    APPROVAL_PENDING = "approval_pending"  # 等待审批（Human-in-the-loop）


class AgentSession(BaseModelMixin, Base):
    """Agent 执行会话表"""
    __tablename__ = "agent_sessions"

    # ==================== 任务信息 ====================
    task_description: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="用户输入的自然语言任务描述",
    )
    session_status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status_enum", create_constraint=True),
        default=SessionStatus.PENDING,
        index=True,
        comment="会话当前状态",
    )

    # ==================== Agent 执行记录 ====================
    current_step: Mapped[int] = mapped_column(
        default=0,
        comment="当前执行到的步骤数",
    )
    total_steps: Mapped[int] = mapped_column(
        default=0,
        comment="规划的总步骤数",
    )
    execution_log: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="执行日志摘要（JSON 格式），保存核心步骤的快照",
    )

    # ==================== 成本与耗时 ====================
    llm_cost: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0"),
        comment="本次会话的 LLM API 调用总费用（USD）",
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="实际开始执行时间",
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="执行完成时间",
    )

    # ==================== 错误信息 ====================
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="失败时的错误消息",
    )

    # ==================== 关联信息 ====================
    # session_id 本身就是主键 id 的别名
    # 但为了日志可读性，提供一个可人工辨识的短 ID
    session_tag: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
        comment="人工可读的会话标签（可选）",
    )
