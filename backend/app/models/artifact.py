"""
Artifact ORM 模型 — Agent 执行产物的持久化存储

每个 Agent Session 产生的所有产出（截图、日志、思考过程、工具结果等）
统一抽象为 Artifact，支持按类型/步骤号过滤和下载。
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModelMixin, Base


def _utcnow():
    return datetime.now(timezone.utc)


class Artifact(BaseModelMixin, Base):
    """Agent 执行产物"""

    __tablename__ = "artifacts"

    # 关联
    session_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="关联的 AgentSession.id"
    )
    step_number: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None, comment="关联的步骤号（0 = 全局产物）"
    )

    # 产物元数据
    artifact_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="产物类型: screenshot / agent_thought / tool_result / log / extracted_text",
    )
    mime_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="application/octet-stream",
        comment="MIME 类型: image/png / text/plain / application/json",
    )

    # 文件信息
    filename: Mapped[str] = mapped_column(
        String(255), nullable=False, default="",
        comment="原始文件名",
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, default=None,
        comment="文件系统路径（相对 backend/data/ 或绝对路径）",
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="文件大小（字节）",
    )

    # 结构化数据（JSON）
    data_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True, default=None, comment="结构化元数据",
    )

    # 文本内容（小文本直接存储，避免查文件系统）
    text_content: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None, comment="文本内容（直接存储）",
    )

    # 时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
