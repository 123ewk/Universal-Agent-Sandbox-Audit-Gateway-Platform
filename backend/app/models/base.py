"""
SQLAlchemy ORM 声明式基类与通用 Mixin

设计动机：
  每个数据表都需要 id、created_at、updated_at 三个字段。
 把这些公共字段抽取到 BaseModelMixin 中，所有模型类继承它即可，
 避免在每个模型中重复定义。

使用方式：
  class AgentSession(BaseModelMixin, Base):
      __tablename__ = "agent_sessions"
      task_description: str
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 声明式基类
    所有 ORM 模型必须继承的基类，metadata 会自动收集所有子类
    """
    pass


class BaseModelMixin:
    """
    ORM 模型通用 Mixin
    所有业务表都包含的公共字段，通过多重继承混入

    为什么用 Mixin 而非父类？
      SQLAlchemy 2.0 推荐将公共字段放在 Mixin 中，
      因为模型需要继承 DeclarativeBase（映射基类），
      如果 BaseModelMixin 也继承 DeclarativeBase 会导致 MRO 冲突。
      Mixin 不声明 __tablename__，也不继承 DeclarativeBase，
      只定义列，由最终的模型类决定映射到哪张表。
    """

    # ==================== 主键 ====================
    # Integer 自增主键，所有表统一
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        comment="自增主键",
    )

    # ==================== 创建时间 ====================
    # server_default 让数据库生成时间戳，而不是 Python 生成
    # 这样即使 ORM 层面插入时不传值，数据库也会自动填充
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间（数据库生成）",
    )

    # ==================== 更新时间 ====================
    # onupdate 在每次更新行时自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间（数据库生成）",
    )
