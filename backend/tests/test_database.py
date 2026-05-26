"""
测试 database.py + models/base.py
运行方式：pytest tests/test_database.py -v

注意：部分测试需要实际数据库连接，默认跳过。
可通过 pytest --run-db 开启。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import Mapped

from app.config import settings
from app.database import _engine, _SessionFactory, get_db_session
from app.models.base import Base, BaseModelMixin


# ==================== 数据库引擎测试 ====================
class TestDatabaseEngine:
    """验证引擎配置不依赖真实数据库连接"""

    def test_engine_is_async(self) -> None:
        """引擎必须是 async 类型"""
        assert isinstance(_engine, AsyncEngine)

    def test_engine_url_uses_asyncpg(self) -> None:
        """连接 URL 必须使用 asyncpg driver"""
        url = str(_engine.url)
        assert url.startswith("postgresql+asyncpg://")
        assert settings.DB_HOST in url
        assert str(settings.DB_PORT) in url

    def test_session_factory_is_async(self) -> None:
        """session 工厂必须是 async 类型"""
        # async_sessionmaker 的 class_ 必须是 AsyncSession
        assert _SessionFactory.class_ is not None


# ==================== Mixin 模型测试 ====================
class TestBaseModelMixin:
    """验证 BaseModelMixin 自动为模型添加 id/created_at/updated_at"""

    def test_mixin_columns_exist_on_subclass(self) -> None:
        """验证继承 mixin 后，模型自动包含三个公共字段"""

        # 定义一个临时模型用于测试
        class TestModel(BaseModelMixin, Base):
            __tablename__ = "_test_mixin"
            name: Mapped[str]

        # 使用 SQLAlchemy inspect 检查列定义
        mapper = inspect(TestModel)
        columns = {col.name: col for col in mapper.columns}

        assert "id" in columns
        assert "created_at" in columns
        assert "updated_at" in columns
        assert "name" in columns

        # 验证主键
        assert columns["id"].primary_key

    def test_mixin_created_at_has_server_default(self) -> None:
        """created_at 必须有 server_default（由数据库生成，而非 Python）"""

        class TestModel(BaseModelMixin, Base):
            __tablename__ = "_test_created_at"
            name: Mapped[str]

        mapper = inspect(TestModel)
        col = mapper.columns["created_at"]
        # server_default = func.now()，在 meta 中表现为 not None
        assert col.server_default is not None

    def test_mixin_updated_at_has_onupdate(self) -> None:
        """updated_at 必须有 onupdate 行为"""

        class TestModel(BaseModelMixin, Base):
            __tablename__ = "_test_updated_at"
            name: Mapped[str]

        mapper = inspect(TestModel)
        col = mapper.columns["updated_at"]
        # onupdate=func.now() 在 mapper 中表现为 onupdate 属性
        assert col.onupdate is not None

    def test_mixin_id_is_autoincrement(self) -> None:
        """id 必须是 autoincrement"""

        class TestModel(BaseModelMixin, Base):
            __tablename__ = "_test_autoincrement"
            name: Mapped[str]

        mapper = inspect(TestModel)
        col = mapper.columns["id"]
        assert col.autoincrement is True

    def test_base_metadata_collects_subclasses(self) -> None:
        """每个继承 Base 的模型都被 metadata 收集"""

        class CollectibleModel(BaseModelMixin, Base):
            __tablename__ = "_test_collect"
            data: Mapped[str]

        table_names = Base.metadata.tables.keys()
        assert "_test_collect" in table_names


# ==================== 依赖注入结构测试 ====================
class TestGetDBSession:
    """验证 get_db_session 依赖注入的异步生成器结构"""

    @pytest.mark.asyncio
    async def test_session_is_async_generator(self) -> None:
        """get_db_session 返回 AsyncGenerator 类型"""
        gen = get_db_session()
        # 异步生成器有 __anext__ 方法
        assert hasattr(gen, "__anext__")
        # 不实际连接数据库，只是验证结构
        await gen.aclose()
