"""
AuditGateway 集成测试：需要真实 PostgreSQL 连接

测试范围：
  1. 常规 Skill 调用（L1-L3）→ 直接执行
  2. L4 Skill 调用 → 创建审批记录
  3. L5 高危操作 → 直接拦截
  4. 审批通过后执行

测试策略：
  - 使用 NullPool + 独立 engine（避免 asyncpg 跨事件循环问题）
  - 每个测试前初始化表结构
  - 使用全局 registry 确保 Skill 已注册
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
from app.skills.base import SkillContext, SkillResult
from app.skills.registry import registry
from app.skills.browser import GotoSkill, ClickSkill
from app.skills.shell import RunCommandSkill
from app.engine.gateway import AuditGateway, ApprovalRequired

# 确保所有 Skill 已注册
import app.skills.browser  # noqa: F401
import app.skills.file     # noqa: F401
import app.skills.shell    # noqa: F401
_ = registry.discover()


@pytest.fixture(autouse=True)
async def setup_tables():
    """每个测试前确保表存在，并清空数据防止跨测试污染"""
    eng = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            __import__("sqlalchemy").text(
                "TRUNCATE TABLE approval_records, audit_logs, agent_sessions RESTART IDENTITY CASCADE"
            )
        )
    yield
    await eng.dispose()


@pytest.fixture
async def db() -> AsyncSession:
    """
    每个测试一个独立 session

    使用独立的 engine（NullPool），发送 SET session 级的配置后返回。
    """
    eng = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    session = AsyncSession(bind=eng, expire_on_commit=False)
    yield session
    await session.close()
    await eng.dispose()


@pytest.fixture
async def test_session(db) -> int:
    """创建一个测试用的 AgentSession，返回其 ID"""
    session = AgentSession(
        task_description="Integration test session",
        session_status=SessionStatus.PENDING,
        current_step=0,
        total_steps=0,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session.id


@pytest.fixture
def gateway():
    return AuditGateway()


@pytest.fixture
def context(test_session):
    return SkillContext(session_id=test_session, request_id="test-req")


# ====================================================================
# Test Suite: AuditGateway
# ====================================================================


class TestAuditGateway:
    """验证审计网关的核心流程"""

    async def test_l1_skill_direct_execution(self, gateway, context, db):
        """验证：L1 Skill（Goto）直接执行，写入审计日志"""
        result = await gateway.invoke(
            "browser_goto", {"url": "https://example.com"}, context, db,
        )
        assert isinstance(result, SkillResult)
        assert result.success is True

        # 验证审计日志已写入
        logs = (await db.execute(select(AuditLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].action_type == "browser_goto"
        assert logs[0].approved is True
        assert logs[0].success is True

    async def test_l2_skill_click_is_logged(self, gateway, context, db):
        """验证：L2 Click 放行并记录日志"""
        result = await gateway.invoke(
            "browser_click", {"selector": "#btn"}, context, db,
        )
        assert isinstance(result, SkillResult)
        assert result.success is True

    async def test_l4_skill_requires_approval(self, gateway, context, db):
        """验证：L4 Shell 执行需要创建审批记录"""
        result = await gateway.invoke(
            "shell_run", {"command": "ls -la"}, context, db,
        )
        assert isinstance(result, ApprovalRequired)
        assert result.approval_record_id > 0
        assert result.audit_log_id > 0

        # 验证 AuditLog 存在且 approved=None（未审批）
        log = (await db.execute(
            select(AuditLog).where(AuditLog.id == result.audit_log_id)
        )).scalar_one()
        assert log.is_high_risk is True
        assert log.approved is None  # 尚未审批

        # 验证 ApprovalRecord 存在且状态为 PENDING
        approval = (await db.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == result.approval_record_id)
        )).scalar_one()
        assert approval.status == ApprovalStatus.PENDING
        assert approval.risk_score >= 61

    async def test_l4_skill_can_be_approved_and_executed(self, gateway, context, db):
        """验证：审批通过后可以执行被暂停的 Skill"""
        # 步骤 1: 发起 Shell 调用 → 触发审批
        result = await gateway.invoke(
            "shell_run", {"command": "ls -la"}, context, db,
        )
        assert isinstance(result, ApprovalRequired)
        approval_id = result.approval_record_id

        # 步骤 2: 模拟用户审批通过
        approval = (await db.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
        )).scalar_one()
        approval.status = ApprovalStatus.APPROVED
        await db.flush()

        # 步骤 3: 执行审批后的 Skill
        exec_result = await gateway.execute_approved(approval_id, context, db)
        assert isinstance(exec_result, SkillResult)
        assert exec_result.success is True

    async def test_l5_blocked_url_intercepted(self, gateway, context, db):
        """验证：L5 拦截后不放行也不创建审批"""
        result = await gateway.invoke(
            "browser_goto", {"url": "file:///etc/passwd"}, context, db,
        )
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "拦截" in result.error

        # 审计日志中有拦截记录
        logs = (await db.execute(select(AuditLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].success is False
        assert logs[0].error_detail is not None

    async def test_nonexistent_skill_returns_error(self, gateway, context, db):
        """验证：不存在的 Skill 名返回错误"""
        result = await gateway.invoke(
            "nonexistent_skill", {}, context, db,
        )
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "未找到" in result.error or "不存在" in result.error

    async def test_approval_before_approve_rejected(self, gateway, context, db):
        """验证：审批尚未通过时 execute_approved 返回错误"""
        # 发起需要审批的调用
        result = await gateway.invoke(
            "shell_run", {"command": "echo hello"}, context, db,
        )
        assert isinstance(result, ApprovalRequired)

        # 还未审批就尝试执行
        exec_result = await gateway.execute_approved(result.approval_record_id, context, db)
        assert isinstance(exec_result, SkillResult)
        assert exec_result.success is False
        assert "尚未通过" in exec_result.error

    async def test_skill_timing_is_recorded(self, gateway, context, db):
        """验证：技能执行耗时被记录到审计日志"""
        await gateway.invoke(
            "browser_goto", {"url": "https://example.com"}, context, db,
        )
        logs = (await db.execute(select(AuditLog))).scalars().all()
        assert logs[0].execution_time_ms >= 0

    async def test_bypass_approval_param_skips_approval(self, gateway, context, db):
        """验证：bypass_approval=True 跳过审批直接执行"""
        result = await gateway.invoke(
            "shell_run", {"command": "ls -la"}, context, db,
            bypass_approval=True,
        )
        assert isinstance(result, SkillResult)
        assert result.success is True

        # 验证无审批记录
        approvals = (await db.execute(select(ApprovalRecord))).scalars().all()
        assert len(approvals) == 0
