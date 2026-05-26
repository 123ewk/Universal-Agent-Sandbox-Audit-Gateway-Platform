"""
AuditLog 模型

记录 Agent 每⼀步操作的不可变审计日志。
这是整个平台"可审计性"的核心表：
每一条日志记录一个 Agent 操作的完整上下文（谁、什么时间、做了什么、结果如何）。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, JSON, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModelMixin


class AuditLog(BaseModelMixin, Base):
    """
    Agent 操作审计日志表

    每一条记录代表 Agent 的一个原子操作（导航、点击、输入、截图等）。
    索引设计说明：
      idx_session_step：按 session_id + step_number 查询是最高频的查询模式
    """
    __tablename__ = "audit_logs"

    # ==================== 所属会话 ====================
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的会话 ID",
    )
    step_number: Mapped[int] = mapped_column(
        default=0,
        comment="此日志属于执行过程中的第几步",
    )

    # ==================== 操作内容 ====================
    action_type: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="操作类型：navigate / click / type / screenshot / think / finish / error",
    )
    action_input: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="操作的输⼊参数（如点击的目标元素、导航的 URL）",
    )
    action_output: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="操作的输出结果（如截图路径、执行状态）",
    )

    # ==================== 安全标记 ====================
    is_high_risk: Mapped[bool] = mapped_column(
        default=False,
        comment="是否被标记为高危操作",
    )
    risk_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="高危标记的原因描述",
    )
    approved: Mapped[Optional[bool]] = mapped_column(
        nullable=True,
        comment="高危操作是否已通过人类审批（None=未审批，True=已通过，False=已拒绝）",
    )

    # ==================== 执行状态 ====================
    success: Mapped[bool] = mapped_column(
        default=True,
        comment="操作是否执行成功",
    )
    error_detail: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="操作失败时的详细错误",
    )
    execution_time_ms: Mapped[int] = mapped_column(
        default=0,
        comment="操作执行耗时（毫秒）",
    )

    # ==================== 时间戳 ====================
    # 覆盖父类的 created_at，因为审计日志可能需要独立的时问戳语义
    # （如翻录历史日志时的批量插⼊，使用原始时间而非当前时间）
    action_taken_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="操作的原始执行时间",
    )
