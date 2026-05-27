"""
异步数据库引擎与会话管理

职责：
  1. 创建 SQLAlchemy async engine（连接池）
  2. 提供 async session 工厂，给 FastAPI 依赖注入使用
  3. 提供生命周期管理（启动时建表、关闭时释放连接）

使用方式：
  from app.database import get_db_session
  @router.get("/")
  async def handler(db: AsyncSession = Depends(get_db_session)):
      ...
"""
import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger(__name__)


# ==================== 引擎与 session 工厂 ====================

_engine = create_async_engine(
    settings.database_url,
    echo=settings.DB_ECHO,               # 打印 SQL 日志（仅供调试）
    pool_size=10,                         # 连接池保持 10 个连接
    max_overflow=20,                      # 最多允许额外创建 20 个连接
    pool_pre_ping=True,                   # 每次从池中取连接前先 ping 一下，
                                          # 避免拿到断开的连接（生产关键）
    pool_recycle=3600,                    # 连接最多存活 1 小时，之后回收重建
)

_SessionFactory = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,               # commit 后不自动过期，避免懒加载异常
)


# ==================== 生命周期管理 ====================

async def init_db() -> None:
    """
    应用启动时调用：创建所有表的元数据
    底层原理：
      metadata.create_all() 会遍历所有继承 Base 的模型，
      在数据库中执行 CREATE TABLE IF NOT EXISTS。
      生产环境应该用 Alembic 迁移管理 schema 变更。
    """
    from app.models.base import Base  # noqa: F811 — 延迟导入，避免循环依赖

    async with _engine.begin() as conn:
        # 对 async engine，表创建操作需要在连接中执行,确保表创建操作在一个事务中执行，失败时会自动回滚。
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表结构检查/创建完成")


async def close_db() -> None:
    """应用关闭时调用：优雅关闭连接池"""
    await _engine.dispose()
    logger.info("数据库连接池已释放")


# ==================== FastAPI 依赖注入 ====================

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入：每次请求获取一个独立数据库会话

    使用 yield 而非 return 是因为 FastAPI 的 Depends 支持
    上下文管理器语义：yield 之前的代码 -> 依赖的构造函数，
    yield 之后的代码 -> 在请求结束后自动执行（清理/释放）。

    如果用 return 则无法在请求结束后自动 close session。
    """
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()       # 请求成功 -> 自动提交
        except Exception:
            await session.rollback()     # 请求异常 -> 自动回滚
            raise                        # 重新抛出，不吞异常
        finally:
            await session.close()        # 归还 session 到连接池
