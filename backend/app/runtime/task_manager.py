"""
TaskManager — 后台任务生命周期管理

职责：
  1. 通过 asyncio.create_task 启动 Agent 后台执行
  2. 跟踪运行中任务（按 session_id 索引）
  3. 支持任务取消和状态查询

原则：
  HTTP 请求立即返回 202，Agent 后台运行。

使用方式：
  tm = get_task_manager()
  tm.start(session_id=42, coro=runtime.run(42, "打开百度"))
  status = tm.get_status(42)  # → "running" | "completed" | "not_found"
  tm.cancel(42)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.runtime.session_manager import get_session_manager

logger = logging.getLogger(__name__)


@dataclass
class TaskHandle:
    """单个后台任务的句柄"""
    session_id: int
    task: asyncio.Task
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskManager:
    """后台任务管理器"""

    def __init__(self) -> None:
        self._tasks: dict[int, TaskHandle] = {}

    # ================================================================
    # 任务操作
    # ================================================================

    def start(
        self,
        session_id: int,
        coro: Awaitable,
        name: str = "",
    ) -> asyncio.Task:
        """
        启动一个后台任务

        Args:
            session_id: 关联的 Session ID
            coro:       要执行的协程
            name:       任务名称（用于调试）

        Returns:
            asyncio.Task 实例

        Raises:
            ValueError: 该 Session 已有运行中任务
        """
        if session_id in self._tasks and not self._tasks[session_id].task.done():
            raise ValueError(f"Session {session_id} 已有运行中任务")

        task = asyncio.create_task(coro, name=name or f"agent-{session_id}")
        self._tasks[session_id] = TaskHandle(session_id=session_id, task=task)

        # 注册完成回调 → 更新 SessionManager
        def _on_done(t: asyncio.Task) -> None:
            sm = get_session_manager()
            try:
                exc = t.exception()
                if exc:
                    logger.error("任务执行异常: session=%d, error=%s", session_id, exc)
                    sm.update(session_id, status="failed", error_message=str(exc))
                else:
                    result = t.result()
                    logger.info("任务执行完成: session=%d", session_id)
                    if result is not None:
                        sm.update(session_id, status=str(getattr(result, "agent_status", "completed")))
            except asyncio.CancelledError:
                sm.update(session_id, status="cancelled")
            except Exception as exc:
                sm.update(session_id, status="failed", error_message=str(exc))

        task.add_done_callback(_on_done)

        sm = get_session_manager()
        sm.update(session_id, status="running")

        logger.info("后台任务已启动: session=%d, name=%s", session_id, task.get_name())
        return task

    def cancel(self, session_id: int) -> bool:
        """
        取消指定 Session 的任务

        Returns:
            True 如果成功取消，False 如果无任务或已完成
        """
        handle = self._tasks.get(session_id)
        if handle is None:
            return False
        if handle.task.done():
            return False

        handle.task.cancel()
        logger.info("任务已取消: session=%d", session_id)
        return True

    def get_status(self, session_id: int) -> str:
        """查询任务状态"""
        handle = self._tasks.get(session_id)
        if handle is None:
            return "not_found"
        if handle.task.done():
            if handle.task.exception():
                return "failed"
            if handle.task.cancelled():
                return "cancelled"
            return "completed"
        return "running"

    def get_task(self, session_id: int) -> Optional[asyncio.Task]:
        """获取任务的 asyncio.Task 句柄"""
        handle = self._tasks.get(session_id)
        return handle.task if handle else None

    def is_running(self, session_id: int) -> bool:
        return self.get_status(session_id) == "running"

    def active_count(self) -> int:
        return sum(1 for h in self._tasks.values() if not h.task.done())

    def list_active(self) -> list[int]:
        return [h.session_id for h in self._tasks.values() if not h.task.done()]


# ====================================================================
# 全局单例
# ====================================================================

_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
