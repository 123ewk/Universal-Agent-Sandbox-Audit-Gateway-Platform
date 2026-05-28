"""
Phase 7 — WebSocket 推流 + 审计审批测试

测试范围：
  - WSMessage / EventType 协议
  - ConnectionManager 连接/广播/心跳
  - AuditPolicy 行为模式检测
  - ApprovalManager 审批暂停/恢复
"""
import asyncio
import pytest

from app.ws.protocol import (
    WSMessage, EventType, StepPayload, ApprovalPayload, RiskPayload,
    make_message, heartbeat, connected, agent_step_completed, approval_required,
    audit_risk_detected,
)
from app.ws.manager import ConnectionManager
from app.audit.policies import AuditPolicy, PolicyAssessment, PolicyTrigger
from app.audit.approval import ApprovalManager, ApprovalRequest, ApprovalStatus
from app.agent.state import AgentState, PlanStep, StepRecord


class TestEventProtocol:
    """WebSocket 事件协议"""

    def test_event_type_enum(self):
        assert EventType.AGENT_STEP_COMPLETED.value == "agent.step.completed"
        assert EventType.APPROVAL_REQUIRED.value == "approval.required"
        assert EventType.SANDBOX_SCREENSHOT.value == "sandbox.screenshot"

    def test_ws_message(self):
        msg = WSMessage(event="test.event", session_id=42, payload={"key": "val"})
        d = msg.to_dict()
        assert d["event"] == "test.event"
        assert d["session_id"] == 42
        assert d["payload"]["key"] == "val"
        assert "timestamp" in d

    def test_make_message_with_model(self):
        payload = StepPayload(step_number=1, skill_name="browser_goto", success=True)
        msg = make_message(EventType.AGENT_STEP_COMPLETED, 42, payload)
        assert msg.event == "agent.step.completed"
        assert msg.payload["skill_name"] == "browser_goto"

    def test_make_message_with_dict(self):
        msg = make_message(EventType.AGENT_STARTED, 1, {"task": "test"})
        assert msg.payload["task"] == "test"

    def test_heartbeat(self):
        msg = heartbeat(42)
        assert msg.event == "system.heartbeat"

    def test_connected(self):
        msg = connected(42)
        assert msg.event == "system.connected"

    def test_step_completed(self):
        payload = StepPayload(step_number=2, skill_name="browser_click", execution_time_ms=150)
        msg = agent_step_completed(42, payload)
        assert msg.event == "agent.step.completed"
        assert msg.payload["execution_time_ms"] == 150

    def test_approval_required(self):
        payload = ApprovalPayload(approval_id=1, skill_name="shell_run", risk_score=70)
        msg = approval_required(42, payload)
        assert msg.event == "approval.required"
        assert msg.payload["risk_score"] == 70

    def test_risk_detected(self):
        payload = RiskPayload(risk_score=60, risk_level=3, reasons=["连续失败"])
        msg = audit_risk_detected(42, payload)
        assert msg.event == "audit.risk.detected"


class TestConnectionManager:
    """ConnectionManager 连接管理"""

    class MockWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data):
            self.sent.append(data)
        async def close(self):
            self.closed = True

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        mgr = ConnectionManager(heartbeat_interval=999)  # disable heartbeat for test
        ws = self.MockWebSocket()

        await mgr.connect(ws, session_id=1)
        assert mgr.subscriber_count(1) == 1
        assert mgr.total_connections() == 1

        await mgr.disconnect(ws, session_id=1)
        assert mgr.subscriber_count(1) == 0

    @pytest.mark.asyncio
    async def test_broadcast(self):
        mgr = ConnectionManager(heartbeat_interval=999)
        ws1 = self.MockWebSocket()
        ws2 = self.MockWebSocket()

        await mgr.connect(ws1, session_id=1)
        await mgr.connect(ws2, session_id=1)

        msg = make_message(EventType.AGENT_STEP_COMPLETED, 1,
                          StepPayload(step_number=1, skill_name="test"))
        sent = await mgr.broadcast(1, msg)
        assert sent == 2
        assert len(ws1.sent) == 2  # connected + broadcast
        assert len(ws2.sent) == 2

    @pytest.mark.asyncio
    async def test_broadcast_nonexistent_session(self):
        mgr = ConnectionManager()
        sent = await mgr.broadcast(999, {"event": "test"})
        assert sent == 0

    @pytest.mark.asyncio
    async def test_active_sessions(self):
        mgr = ConnectionManager(heartbeat_interval=999)
        ws = self.MockWebSocket()
        await mgr.connect(ws, session_id=42)
        assert 42 in mgr.active_sessions()

    @pytest.mark.asyncio
    async def test_cleanup_session(self):
        mgr = ConnectionManager(heartbeat_interval=999)
        ws = self.MockWebSocket()
        await mgr.connect(ws, session_id=99)
        await mgr.cleanup_session(99)
        assert mgr.subscriber_count(99) == 0


class TestAuditPolicy:
    """AuditPolicy 行为模式检测"""

    def _make_step(self, step_number, skill_name, success=True, params=None):
        plan = PlanStep(step_number=step_number, description="test",
                       skill_name=skill_name, skill_params=params or {})
        from datetime import datetime, timezone
        return StepRecord(
            step_number=step_number, plan_step=plan, success=success,
            finished_at=datetime.now(timezone.utc),
        )

    def test_empty_steps(self):
        policy = AuditPolicy()
        result = policy.assess([])
        assert result.total_score == 0
        assert result.requires_approval is False

    def test_consecutive_failures(self):
        policy = AuditPolicy()
        steps = [
            self._make_step(i, "browser_click", success=False)
            for i in range(1, 4)
        ]
        result = policy.assess(steps)
        assert result.total_score >= 60
        triggers = [t.rule_name for t in result.triggers]
        assert "consecutive_failures" in triggers

    def test_shell_command(self):
        policy = AuditPolicy()
        steps = [
            self._make_step(1, "browser_goto", success=True),
            self._make_step(2, "shell_run", success=True),
        ]
        result = policy.assess(steps)
        triggers = [t.rule_name for t in result.triggers]
        assert "shell_command" in triggers

    def test_dangerous_combo(self):
        policy = AuditPolicy()
        steps = [
            self._make_step(1, "browser_goto", success=True,
                          params={"url": "https://bank.com/transfer"}),
            self._make_step(2, "browser_click", success=True,
                          params={"selector": "#confirm-payment"}),
        ]
        result = policy.assess(steps)
        triggers = [t.rule_name for t in result.triggers]
        assert "dangerous_combo" in triggers

    def test_normal_steps_pass(self):
        policy = AuditPolicy()
        steps = [
            self._make_step(1, "browser_goto", success=True),
            self._make_step(2, "browser_type", success=True),
            self._make_step(3, "browser_screenshot", success=True),
        ]
        result = policy.assess(steps)
        assert result.total_score < 40
        assert result.requires_approval is False
        assert result.should_pause is False

    def test_high_risk_url(self):
        policy = AuditPolicy()
        steps = [
            self._make_step(1, "browser_goto", success=True,
                          params={"url": "https://admin.internal/dashboard"}),
        ]
        result = policy.assess(steps)
        triggers = [t.rule_name for t in result.triggers]
        assert "high_risk_url_interaction" in triggers

    def test_threshold_approval(self):
        """超过阈值触发审批"""
        policy = AuditPolicy()
        policy.APPROVAL_THRESHOLD = 20  # 降低阈值
        steps = [
            self._make_step(1, "shell_run", success=True),
        ]
        result = policy.assess(steps)
        assert result.requires_approval is True


class TestApprovalManager:
    """ApprovalManager 审批流程"""

    @pytest.mark.asyncio
    async def test_request_and_approve(self):
        mgr = ApprovalManager(timeout_seconds=10)

        # 模拟 Agent 端：创建审批请求并等待
        async def agent_side():
            req = await mgr.request(session_id=1, skill_name="shell_run",
                                    step_number=3, risk_score=70)
            return req

        # 模拟审批端：在 0.1s 后批准
        async def approval_side():
            await asyncio.sleep(0.1)
            pending = mgr.get_pending()
            if pending:
                await mgr.approve(pending[0].id)

        # 并发执行
        agent_task = asyncio.create_task(agent_side())
        approval_task = asyncio.create_task(approval_side())

        req = await agent_task
        await approval_task

        assert req.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_request_and_deny(self):
        mgr = ApprovalManager(timeout_seconds=10)

        async def agent_side():
            return await mgr.request(session_id=1, skill_name="file_delete",
                                    step_number=5, risk_score=85)

        async def approval_side():
            await asyncio.sleep(0.1)
            pending = mgr.get_pending()
            if pending:
                await mgr.deny(pending[0].id, reason="非工作时间不允许删除文件")

        agent_task = asyncio.create_task(agent_side())
        approval_task = asyncio.create_task(approval_side())

        req = await agent_task
        await approval_task

        assert req.status == ApprovalStatus.DENIED

    @pytest.mark.asyncio
    async def test_request_timeout(self):
        mgr = ApprovalManager(timeout_seconds=1)  # 1 秒超时

        req = await mgr.request(session_id=1, skill_name="test",
                                step_number=1, risk_score=50)
        assert req.status == ApprovalStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_get_pending(self):
        mgr = ApprovalManager(timeout_seconds=30)

        # 异步创建请求但不等待
        async def create():
            await mgr.request(session_id=1, skill_name="test", risk_score=50)

        task = asyncio.create_task(create())
        await asyncio.sleep(0.1)

        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].skill_name == "test"

        task.cancel()

    @pytest.mark.asyncio
    async def test_get_pending_by_session(self):
        mgr = ApprovalManager(timeout_seconds=30)

        async def create_req(sid):
            await mgr.request(session_id=sid, skill_name="test", risk_score=50)

        asyncio.create_task(create_req(1))
        asyncio.create_task(create_req(2))
        await asyncio.sleep(0.1)

        assert len(mgr.get_pending(session_id=1)) == 1
        assert len(mgr.get_pending(session_id=2)) == 1

    @pytest.mark.asyncio
    async def test_approve_nonexistent(self):
        mgr = ApprovalManager()
        with pytest.raises(ValueError, match="不存在"):
            await mgr.approve(999)

    @pytest.mark.asyncio
    async def test_history(self):
        mgr = ApprovalManager(timeout_seconds=1)

        req = await mgr.request(session_id=1, skill_name="test",
                                step_number=1, risk_score=50)
        # 超时后应出现在历史中
        history = mgr.get_history()
        assert len(history) >= 1


class TestPhase7Integration:
    """Phase 7 端到端集成"""

    @pytest.mark.asyncio
    async def test_detector_with_ws(self):
        """BehaviorDetector 检测到风险时推送 WS"""
        from app.audit.detector import BehaviorDetector
        from app.audit.policies import AuditPolicy

        policy = AuditPolicy()
        policy.APPROVAL_THRESHOLD = 10
        detector = BehaviorDetector(policies=policy)

        state = AgentState(session_id=1, task_description="test")
        plan = PlanStep(step_number=1, description="test", skill_name="shell_run")
        state.execution_history.append(
            StepRecord(step_number=1, plan_step=plan, success=True)
        )

        # 无 WS manager 也不应崩溃
        assessment = await detector.analyze(state, ws_manager=None)
        assert assessment.total_score > 0
        assert len(assessment.triggers) > 0

    @pytest.mark.asyncio
    async def test_approval_flow(self):
        """完整审批流：request → approve → Agent 恢复"""
        mgr = ApprovalManager(timeout_seconds=5)

        # 模拟 Agent
        async def agent():
            return await mgr.request(
                session_id=1, skill_name="shell_run",
                step_number=1, risk_score=85,
                risk_reasons=["执行 Shell 命令"],
            )

        # 模拟管理员
        async def admin():
            await asyncio.sleep(0.15)
            pending = mgr.get_pending()
            await mgr.approve(pending[0].id, resolved_by="admin")

        agent_task = asyncio.create_task(agent())
        admin_task = asyncio.create_task(admin())

        result = await agent_task
        await admin_task

        assert result.status == ApprovalStatus.APPROVED
        assert result.resolved_by == "admin"
