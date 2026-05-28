"""
MemoryVector — pgvector 向量记忆模型

设计动机：
  Agent 执行过程中产生的观察值、用户纠错、决策路径等信息，
  需要以向量形式存储以便后续语义检索（如"上次遇到类似错误怎么处理的"）。

  不引入独立向量数据库（Pinecone/Weaviate/Qdrant），
  使用 PostgreSQL pgvector 扩展在同一个数据库内完成向量存储与检索，
  降低运维复杂度，且向量数据与关系数据天然可 JOIN。

表结构：
  memory_vectors(session_id, step_id, memory_type, content, embedding(1536d), metadata, ...)

索引策略：
  ivfflat — 适合百万级以下数据量，构建快，召回率 > 95%

使用方式：
  from app.models.memory import MemoryVector
  # embedding 生成由 LLMClient 或专门的 EmbeddingService 完成
"""
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModelMixin

# 默认 embedding 维度（text-embedding-3-small = 1536, text-embedding-ada-002 = 1536）
DEFAULT_EMBEDDING_DIM = 1536


class MemoryVector(BaseModelMixin, Base):
    """
    Agent 向量记忆表

    每一条记录代表 Agent 执行过程中的一个"记忆片段"，
    包含原始文本内容、向量嵌入和可查询的结构化元数据。

    为什么存 content 原文？
      embedding 用于语义相似度检索，content 用于展示给 LLM 参考。
      检索时返回 content + metadata，不直接暴露向量。
    """
    __tablename__ = "memory_vectors"

    # ==================== 关联信息 ====================
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属 Agent 会话 ID",
    )
    step_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="产生该记忆的执行步骤号（从 1 开始）",
    )

    # ==================== 记忆分类 ====================
    memory_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="observation",
        comment="记忆类型: observation(观察) / decision(决策) / error(错误) / correction(纠错) / reflection(反思)",
    )

    # ==================== 内容 ====================
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="记忆的原始文本内容（检索后注入 LLM 上下文）",
    )

    # ==================== 向量嵌入 ====================
    embedding: Mapped[Any] = mapped_column(
        Vector(DEFAULT_EMBEDDING_DIM),
        nullable=True,
        comment="文本的向量嵌入（1536 维），用于语义相似度检索",
    )

    # ==================== 元数据 ====================
    metadata_: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        name="metadata",
        comment="结构化元数据（如 skill_name, url, error_code, risk_level 等），支持 JSONB 查询",
    )

    # ==================== 访问计数 ====================
    access_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="该记忆被检索命中的次数（用于重要性加权）",
    )
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近一次被检索命中的时间",
    )

    # ==================== 索引 ====================
    __table_args__ = (
        # 复合索引：按会话 + 类型查询（最常用路径）
        Index("ix_memory_session_type", "session_id", "memory_type"),
        # ivfflat 向量索引：余弦距离检索
        # 注意：ivfflat 索引需要在表中有一定数据量后创建才能生效
        # 建表时先不创建，在数据量达到 1000+ 后手动创建
        Index(
            "ix_memory_embedding_ivfflat",
            embedding,
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return (
            f"<MemoryVector id={self.id} session={self.session_id} "
            f"step={self.step_id} type={self.memory_type} "
            f"content='{preview}...'>"
        )
