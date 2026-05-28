"""
SessionManager — 唯一状态管理中心

职责：
  1. 管理所有 Agent Session 的状态（Map<session_id, SessionState>）
  2. 维护每个 Session 的事件日志（EventLog）
  3. 提供统一的状态查询接口

原则：
  不要把状态放 router。SessionManager 是唯一的状态源。

使用方式：
  mgr = get_session_manager()
  session = mgr.create(42, task_description="打开百度")
  mgr.add_event(42, {"event": "agent.step.completed", ...})
  events = mgr.get_event_log(42)
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ====================================================================
# SessionState — 单个 Session 的完整状态
# ====================================================================

@dataclass
class SessionState:
    session_id: int
    task_description: str = ""
    status: str = "pending"  # pending | planning | running | completed | failed | cancelled
    total_steps: int = 0
    total_steps_executed: int = 0
    progress_pct: float = 0.0
    llm_cost: str = "0"
    tokens_used: int = 0
    error_message: Optional[str] = None

    # Plan/Execution data
    plan_steps: list[dict] = field(default_factory=list)
    execution_history: list[dict] = field(default_factory=list)

    # Event log（完整事件记录，支撑 replay）
    event_log: list[dict] = field(default_factory=list)

    # Browser state
    current_url: str = ""
    page_title: str = ""
    screenshots: list[dict] = field(default_factory=list)

    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def event_count(self) -> int:
        return len(self.event_log)

    @property
    def screenshot_count(self) -> int:
        return len(self.screenshots)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task_description": self.task_description,
            "status": self.status,
            "total_steps": self.total_steps,
            "total_steps_executed": self.total_steps_executed,
            "progress_pct": self.progress_pct,
            "llm_cost": self.llm_cost,
            "tokens_used": self.tokens_used,
            "error_message": self.error_message,
            "current_url": self.current_url,
            "page_title": self.page_title,
            "event_count": len(self.event_log),
            "screenshot_count": len(self.screenshots),
        }


# ====================================================================
# SessionManager — Singleton
# ====================================================================

class SessionManager:
    """
    全局 Session 状态管理器

    特点：
      - 单例模式，全应用只有一个实例
      - 线程安全（asyncio 单线程模型 + dict 操作是原子的）
      - 每个 Session 保存完整 EventLog（不丢失数据）
    """

    def __init__(self) -> None:
        self._sessions: dict[int, SessionState] = {}

    # ================================================================
    # CRUD
    # ================================================================

    def create(
        self,
        session_id: int,
        task_description: str = "",
        total_steps: int = 50,
    ) -> SessionState:
        """创建新 Session"""
        if session_id in self._sessions:
            logger.warning("Session %d 已存在，返回现有实例", session_id)
            return self._sessions[session_id]

        state = SessionState(
            session_id=session_id,
            task_description=task_description,
            total_steps=total_steps,
            status="pending",
        )
        self._sessions[session_id] = state
        logger.info("Session 已创建: id=%d, task='%s'", session_id, task_description[:60])
        return state

    def get(self, session_id: int) -> Optional[SessionState]:
        """获取 Session 状态"""
        return self._sessions.get(session_id)

    def update(self, session_id: int, **kwargs) -> Optional[SessionState]:
        """更新 Session 字段"""
        state = self._sessions.get(session_id)
        if state is None:
            return None
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        return state

    def delete(self, session_id: int) -> None:
        """删除 Session"""
        self._sessions.pop(session_id, None)
        logger.info("Session 已删除: id=%d", session_id)

    def exists(self, session_id: int) -> bool:
        return session_id in self._sessions

    def list_ids(self) -> list[int]:
        return list(self._sessions.keys())

    def count(self) -> int:
        return len(self._sessions)

    # ================================================================
    # Event Log
    # ================================================================

    def add_event(self, session_id: int, event: dict) -> None:
        """向 Session 的事件日志追加一条事件"""
        state = self._sessions.get(session_id)
        if state is None:
            logger.warning("add_event: Session %d 不存在", session_id)
            return
        state.event_log.append(event)
        state.updated_at = datetime.now(timezone.utc).isoformat()

    def get_event_log(self, session_id: int) -> list[dict]:
        """获取 Session 的完整事件日志"""
        state = self._sessions.get(session_id)
        if state is None:
            return []
        return state.event_log

    # ================================================================
    # Execution History
    # ================================================================

    def add_step(self, session_id: int, step: dict) -> None:
        """追加一条执行步骤记录"""
        state = self._sessions.get(session_id)
        if state is None:
            return
        state.execution_history.append(step)
        state.total_steps_executed = len(state.execution_history)
        if state.total_steps > 0:
            state.progress_pct = round(
                state.total_steps_executed / state.total_steps * 100, 1,
            )
        state.updated_at = datetime.now(timezone.utc).isoformat()

    def add_screenshot(self, session_id: int, screenshot: dict) -> None:
        """追加截图记录"""
        state = self._sessions.get(session_id)
        if state is None:
            return
        state.screenshots.append(screenshot)
        state.updated_at = datetime.now(timezone.utc).isoformat()


# ====================================================================
# 全局单例
# ====================================================================

_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
