"""
ORM 模型集成测试：需要真实 PostgreSQL 连接
运行方式：pytest tests/test_models_integration.py -v

Windows 注意事项：
  asyncpg 底层使用 selectors 模块，与 pytest-asyncio 的事件循环管理存在兼容问题。
  解决方案：使用 NullPool 避免连接池跨事件循环复用，每个测试独立使用连接。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.config import settings
from app.models.base import Base
from app.models.session import AgentSession, SessionStatus
from app.models.audit_log import AuditLog
from app.models.approval import ApprovalRecord, ApprovalStatus
from app.schemas.session import SessionResponse
from app.schemas.audit import AuditLogResponse


@pytest.fixture(autouse=True)
async def setup_tables():
    """
    全局自动 fixture：每个测试前确保表存在
    独立的 engine + NullPool，不与其他 fixture 共享
    """
    eng = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await eng.dispose()


@pytest.fixture
async def db() -> AsyncSession:
    """每个测试一个独立 session，用 NullPool 避免跨事件循环问题"""
    eng = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    session = AsyncSession(bind=eng, expire_on_commit=False)
    yield session
    await session.close()
    await eng.dispose()


# ==================== 创建模型测试 ====================
class TestAgentSessionCreate:
    @pytest.mark.asyncio
    async def test_create_session(self, db: AsyncSession) -> None:
        session = AgentSession(
            task_description="帮我查询今天的天气",
            session_status=SessionStatus.PENDING,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        assert session.id > 0
        assert session.task_description == "帮我查询今天的天气"
        assert session.session_status == SessionStatus.PENDING
        assert session.current_step == 0
        assert session.created_at is not None

    @pytest.mark.asyncio
    async def test_create_session_with_full_fields(self, db: AsyncSession) -> None:
        session = AgentSession(
            task_description="登录教务系统导出成绩",
            session_status=SessionStatus.RUNNING,
            current_step=3,
            total_steps=10,
            session_tag="test-001",
            execution_log={"steps": [{"action": "navigate", "url": "https://example.com"}]},
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        assert session.session_tag == "test-001"
        assert session.execution_log["steps"][0]["action"] == "navigate"
        assert session.total_steps == 10


# ==================== 状态流转测试 ====================
class TestSessionStatusTransition:
    @pytest.mark.asyncio
    async def test_status_transition(self, db: AsyncSession) -> None:
        session = AgentSession(task_description="测试状态流转")
        db.add(session)
        await db.commit()

        session.session_status = SessionStatus.RUNNING
        await db.commit()
        await db.refresh(session)

        assert session.session_status == SessionStatus.RUNNING


# ==================== 关系测试 ====================
class TestModelRelations:
    @pytest.mark.asyncio
    async def test_session_has_audit_logs(self, db: AsyncSession) -> None:
        session = AgentSession(task_description="关系测试")
        db.add(session)
        await db.commit()
        await db.refresh(session)

        log1 = AuditLog(
            session_id=session.id, step_number=1,
            action_type="navigate", action_input={"url": "https://example.com"},
        )
        log2 = AuditLog(
            session_id=session.id, step_number=2,
            action_type="click",
        )
        db.add(log1)
        db.add(log2)
        await db.commit()

        stmt = select(AuditLog).where(AuditLog.session_id == session.id).order_by(AuditLog.step_number)
        result = await db.execute(stmt)
        logs = result.scalars().all()

        assert len(logs) == 2
        assert logs[0].action_type == "navigate"
        assert logs[1].action_type == "click"

    @pytest.mark.asyncio
    async def test_approval_record_links(self, db: AsyncSession) -> None:
        session = AgentSession(task_description="审批关系测试")
        db.add(session)
        await db.commit()
        await db.refresh(session)

        log = AuditLog(
            session_id=session.id, step_number=1,
            action_type="click", is_high_risk=True, risk_reason="涉及银行转账",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)

        approval = ApprovalRecord(
            session_id=session.id,
            audit_log_id=log.id,
            risk_type="financial_action",
            risk_description="Agent 尝试点击银行转账按钮",
            risk_score=85,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        assert approval.session_id == session.id
        assert approval.audit_log_id == log.id
        assert approval.status == ApprovalStatus.PENDING
        assert approval.risk_score == 85


# ==================== Schema 序列化测试 ====================
class TestSchemaSerialization:
    @pytest.mark.asyncio
    async def test_session_to_schema(self, db: AsyncSession) -> None:
        session = AgentSession(task_description="Schema 测试", session_tag="schema-test")
        db.add(session)
        await db.commit()
        await db.refresh(session)

        schema = SessionResponse.model_validate(session)
        assert schema.id == session.id
        assert schema.task_description == "Schema 测试"
        assert schema.session_tag == "schema-test"
        assert schema.session_status == SessionStatus.PENDING

    @pytest.mark.asyncio
    async def test_audit_log_to_schema(self, db: AsyncSession) -> None:
        session = AgentSession(task_description="审计日志 Schema 测试")
        db.add(session)
        await db.commit()
        await db.refresh(session)

        log = AuditLog(
            session_id=session.id, step_number=1,
            action_type="navigate",
            action_input={"url": "https://example.com"},
            execution_time_ms=1234,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)

        schema = AuditLogResponse.model_validate(log)
        assert schema.session_id == session.id
        assert schema.action_type == "navigate"
        assert schema.action_input["url"] == "https://example.com"
        assert schema.execution_time_ms == 1234
