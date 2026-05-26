"""
ApprovalRecord 模型

记录 Human-in-the-loop 审批过程的完整上下文。
每当 Agent 检测到高危操作时，系统暂停执行并创建一个审批记录，
等待人类操作员在 Vue 3 前端点击"允许"或"拒绝"。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, JSON, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModelMixin
import enum


class ApprovalStatus(str, enum.Enum):
    """审批状态枚举"""
    PENDING = "pending"          # 等待审批
    APPROVED = "approved"        # 人类已允许
    DENIED = "denied"            # 人类已拒绝
    EXPIRED = "expired"          # 超时未审批，自动过期


class ApprovalRecord(BaseModelMixin, Base):
    """
    审批记录表

    一个 Session 可能产⽣多条审批记录（每触发一次高危操作生成一条）。
    审批通过后 Agent 继续执行，审批拒绝后该步骤被跳过或整个会话终止。
    """
    __tablename__ = "approval_records"

    # ==================== 关联信息 ====================
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的会话 ID",
    )
    audit_log_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("audit_logs.id", ondelete="SET NULL"),
        nullable=True,
        comment="触发审批的那条审计日志 ID",
    )

    # ==================== 高危操作详情 ====================
    risk_type: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="高危类型：sensitive_url / financial_action / file_operation / delete_action / custom",
    )
    risk_description: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="面向人类的危险操作描述，会展示在 Vue 3 审批弹窗中",
    )
    risk_score: Mapped[int] = mapped_column(
        default=50,
        comment="危险等级评分（0-100），越高越危险",
    )
    action_context: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="操作时的完整上下文快照（URL、页面标题、截图路径等）",
    )

    # ==================== 审批状态 ====================
    status: Mapped[ApprovalStatus] = mapped_column(
        default=ApprovalStatus.PENDING,
        comment="审批状态",
    )
    responded_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="人类操作员响应时间",
    )
    response_note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="人类操作员的备注（可选）",
    )

    # ==================== 超时策略 ====================
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="审批超时时间，超过此时间自动标记为 EXPIRED",
    )
