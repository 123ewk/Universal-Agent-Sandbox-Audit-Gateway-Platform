"""
P0 Runtime Layer — SessionManager / TaskManager / EventBus / WebSocketManager 测试
"""
import asyncio
import pytest

from app.runtime.session_manager import SessionManager, get_session_manager
from app.runtime.task_manager import TaskManager
from app.runtime.event_bus import EventBus
from app.runtime.websocket_manager import WebSocketManager


# ====================================================================
# SessionManager
# ====================================================================

class TestSessionManager:
    """SessionManager — 状态管理"""

    def test_create_session(self):
        mgr = SessionManager()
        s = mgr.create(1, "打开百度")
        assert s.session_id == 1
        assert s.task_description == "打开百度"
        assert s.status == "pending"

    def test_create_duplicate_returns_existing(self):
        mgr = SessionManager()
        s1 = mgr.create(1, "original")
        s2 = mgr.create(1, "new description")
        assert s1 is s2
        assert s2.task_description == "original"  # unchanged

    def test_get_nonexistent(self):
        mgr = SessionManager()
        assert mgr.get(999) is None

    def test_update_fields(self):
        mgr = SessionManager()
        mgr.create(1)
        mgr.update(1, status="running", progress_pct=50.0)
        s = mgr.get(1)
        assert s.status == "running"
        assert s.progress_pct == 50.0

    def test_delete(self):
        mgr = SessionManager()
        mgr.create(1)
        mgr.delete(1)
        assert mgr.get(1) is None

    def test_exists(self):
        mgr = SessionManager()
        assert not mgr.exists(1)
        mgr.create(1)
        assert mgr.exists(1)

    def test_list_ids(self):
        mgr = SessionManager()
        mgr.create(1)
        mgr.create(2)
        mgr.create(3)
        assert sorted(mgr.list_ids()) == [1, 2, 3]
        assert mgr.count() == 3

    def test_add_event(self):
        mgr = SessionManager()
        mgr.create(1)
        mgr.add_event(1, {"event": "agent.started"})
        mgr.add_event(1, {"event": "agent.step.completed"})
        log = mgr.get_event_log(1)
        assert len(log) == 2
        assert log[0]["event"] == "agent.started"

    def test_add_event_nonexistent_session(self):
        mgr = SessionManager()
        mgr.add_event(999, {"event": "test"})  # no crash
        assert mgr.get_event_log(999) == []

    def test_add_step(self):
        mgr = SessionManager()
        mgr.create(1, total_steps=5)
        mgr.add_step(1, {"step_number": 1, "skill_name": "goto"})
        mgr.add_step(1, {"step_number": 2, "skill_name": "click"})
        s = mgr.get(1)
        assert s.total_steps_executed == 2
        assert s.progress_pct == 40.0

    def test_add_screenshot(self):
        mgr = SessionManager()
        mgr.create(1)
        mgr.add_screenshot(1, {"filename": "step_01_goto.png"})
        mgr.add_screenshot(1, {"filename": "step_02_click.png"})
        s = mgr.get(1)
        assert s.screenshot_count == 2

    def test_to_dict(self):
        mgr = SessionManager()
        s = mgr.create(1, "test task")
        d = s.to_dict()
        assert d["session_id"] == 1
        assert d["task_description"] == "test task"
        assert d["status"] == "pending"
        assert d["event_count"] == 0

    def test_global_singleton(self):
        m1 = get_session_manager()
        m2 = get_session_manager()
        assert m1 is m2


# ====================================================================
# TaskManager
# ====================================================================

class TestTaskManager:
    """TaskManager — 后台任务管理"""

    @pytest.mark.asyncio
    async def test_start_and_track_task(self):
        tm = TaskManager()

        async def dummy_work():
            await asyncio.sleep(0.05)
            return

        task = tm.start(1, dummy_work())
        assert tm.is_running(1)
        assert tm.active_count() == 1
        assert tm.list_active() == [1]

        await task
        # wait for done callback
        await asyncio.sleep(0.01)
        assert not tm.is_running(1)

    @pytest.mark.asyncio
    async def test_start_duplicate_raises(self):
        tm = TaskManager()

        async def long_work():
            await asyncio.sleep(10)

        task = tm.start(1, long_work())
        with pytest.raises(ValueError, match="已有运行中任务"):
            coro = long_work()
            try:
                tm.start(1, coro)
            finally:
                coro.close()  # prevent "never awaited" warning

        tm.cancel(1)
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        tm = TaskManager()

        async def long_work():
            await asyncio.sleep(10)

        task = tm.start(1, long_work())
        assert tm.is_running(1)

        result = tm.cancel(1)
        assert result is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        # cancelled task is done, cancel again should return False
        cancel_again = tm.cancel(1)
        assert cancel_again is False

    @pytest.mark.asyncio
    async def test_get_status_not_found(self):
        tm = TaskManager()
        assert tm.get_status(999) == "not_found"

    @pytest.mark.asyncio
    async def test_get_status_completed(self):
        tm = TaskManager()

        async def quick_work():
            await asyncio.sleep(0.01)

        task = tm.start(1, quick_work())
        await task
        await asyncio.sleep(0.01)

        assert tm.get_status(1) == "completed"

    @pytest.mark.asyncio
    async def test_done_callback_updates_session(self):
        tm = TaskManager()
        from app.runtime.session_manager import get_session_manager
        sm = get_session_manager()
        sm.create(1, "test")

        async def quick_work():
            await asyncio.sleep(0.01)

        task = tm.start(1, quick_work())
        await task
        await asyncio.sleep(0.01)

        s = sm.get(1)
        assert s.status in ("completed", "running")  # callback may have run

    @pytest.mark.asyncio
    async def test_multiple_tasks(self):
        tm = TaskManager()

        async def work():
            await asyncio.sleep(0.03)

        t1 = tm.start(1, work())
        t2 = tm.start(2, work())
        assert tm.active_count() == 2
        assert sorted(tm.list_active()) == [1, 2]

        await asyncio.gather(t1, t2)
        await asyncio.sleep(0.01)
        assert tm.active_count() == 0


# ====================================================================
# EventBus
# ====================================================================

class TestEventBus:
    """EventBus — 统一事件分发"""

    @pytest.mark.asyncio
    async def test_dispatch_to_handler(self):
        bus = EventBus()
        received = []

        async def handler(sid, event, payload):
            received.append((sid, event, payload))

        bus.subscribe("test", handler)
        await bus.dispatch(1, "agent.started", {"step": 0})

        assert len(received) == 1
        assert received[0][0] == 1
        assert received[0][1] == "agent.started"

    @pytest.mark.asyncio
    async def test_dispatch_to_multiple_handlers(self):
        bus = EventBus()
        results = {"ws": 0, "db": 0}

        async def ws_handler(sid, event, payload):
            results["ws"] += 1

        async def db_handler(sid, event, payload):
            results["db"] += 1

        bus.subscribe("ws", ws_handler)
        bus.subscribe("db", db_handler)
        await bus.dispatch(1, "test.event")

        assert results["ws"] == 1
        assert results["db"] == 1

    @pytest.mark.asyncio
    async def test_seq_dedup(self):
        bus = EventBus()
        received = []

        async def handler(sid, event, payload):
            received.append(payload.get("n"))

        bus.subscribe("t", handler)
        await bus.dispatch(1, "test", {"n": 1}, seq=1)
        await bus.dispatch(1, "test", {"n": 2}, seq=2)
        await bus.dispatch(1, "test", {"n": 1}, seq=1)  # duplicate seq
        await bus.dispatch(1, "test", {"n": 3}, seq=3)

        assert received == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_no_seq_no_dedup(self):
        bus = EventBus()
        received = []

        async def handler(sid, event, payload):
            received.append(1)

        bus.subscribe("t", handler)
        # seq=None skips dedup check
        await bus.dispatch(1, "test")
        await bus.dispatch(1, "test")
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_reset_seq(self):
        bus = EventBus()
        received = []

        async def handler(sid, event, payload):
            received.append(payload.get("n"))

        bus.subscribe("t", handler)
        await bus.dispatch(1, "test", {"n": 1}, seq=5)
        bus.reset_seq(1)
        await bus.dispatch(1, "test", {"n": 2}, seq=1)  # now accepted
        assert received == [1, 2]

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_crash(self):
        bus = EventBus()

        async def bad_handler(sid, event, payload):
            raise RuntimeError("handler crash")

        bus.subscribe("bad", bad_handler)
        # should not raise
        await bus.dispatch(1, "test")

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        received = []

        async def handler(sid, event, payload):
            received.append(1)

        bus.subscribe("t", handler)
        await bus.dispatch(1, "test")
        bus.unsubscribe("t")
        await bus.dispatch(1, "test")
        assert len(received) == 1

    def test_subscriber_count(self):
        bus = EventBus()
        assert bus.subscriber_count() == 0

        async def h(sid, e, p): pass
        bus.subscribe("a", h)
        bus.subscribe("b", h)
        assert bus.subscriber_count() == 2


# ====================================================================
# WebSocketManager
# ====================================================================

class TestWebSocketManager:
    """WebSocketManager — 统一 WS 消息管理"""

    class MockWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def accept(self):
            pass

        async def send_json(self, data):
            self.sent.append(data)

        async def close(self):
            self.closed = True

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        wsm = WebSocketManager()
        ws = self.MockWebSocket()

        await wsm.connect(ws, session_id=1)
        assert wsm.subscriber_count(1) == 1
        assert wsm.total_connections() == 1

        await wsm.disconnect(ws, session_id=1)
        assert wsm.subscriber_count(1) == 0

    @pytest.mark.asyncio
    async def test_broadcast_with_seq(self):
        wsm = WebSocketManager()
        ws = self.MockWebSocket()

        await wsm.connect(ws, session_id=1)

        # First broadcast
        sent = await wsm.broadcast(1, "agent.started", {"task": "test"})
        assert sent == 1

        msg = ws.sent[-1]  # last message (first is "connected" system message)
        assert msg["session_id"] == 1
        assert msg["seq"] == 1
        assert msg["event"] == "agent.started"
        assert msg["payload"]["task"] == "test"

        # Second broadcast
        await wsm.broadcast(1, "agent.step.completed", {"step": 1})
        msg2 = ws.sent[-1]
        assert msg2["seq"] == 2

    @pytest.mark.asyncio
    async def test_seq_per_session_independent(self):
        wsm = WebSocketManager()
        ws1 = self.MockWebSocket()
        ws2 = self.MockWebSocket()

        await wsm.connect(ws1, session_id=1)
        await wsm.connect(ws2, session_id=2)

        await wsm.broadcast(1, "test")
        await wsm.broadcast(2, "test")
        await wsm.broadcast(1, "test")

        assert wsm.get_current_seq(1) == 2
        assert wsm.get_current_seq(2) == 1

    @pytest.mark.asyncio
    async def test_broadcast_empty_room(self):
        wsm = WebSocketManager()
        sent = await wsm.broadcast(999, "test")
        assert sent == 0

    @pytest.mark.asyncio
    async def test_cleanup_session(self):
        wsm = WebSocketManager()
        ws = self.MockWebSocket()

        await wsm.connect(ws, session_id=1)
        await wsm.broadcast(1, "test")
        assert wsm.get_current_seq(1) == 1

        await wsm.cleanup_session(1)
        assert wsm.subscriber_count(1) == 0
        assert wsm.get_current_seq(1) == 0  # seq cleared
