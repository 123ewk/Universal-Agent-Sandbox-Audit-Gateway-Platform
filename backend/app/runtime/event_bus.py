"""
EventBus — 统一事件中枢

职责：
  1. 接收 Runtime 发出的事件
  2. 按事件类型分发到已注册的处理器（WS / DB / Audit）
  3. 每 Session 维护 last_seq 用于去重

设计：
  runtime.emit(event) → EventBus.dispatch(event)
    → ws_handler(event)   → WebSocketManager.broadcast()
    → db_handler(event)   → 写入 event_log
    → audit_handler(event) → 触发审计规则

使用方式：
  bus = get_event_bus()
  bus.subscribe("ws", ws_handler)
  bus.subscribe("db", db_handler)
  await bus.dispatch(session_id=42, event="agent.step.completed", payload={...})
"""
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[[int, str, dict], Awaitable[None]]  # (session_id, event, payload) → None


class EventBus:
    """
    统一事件分发总线

    特性：
      - 支持多订阅者（WS / DB / Audit 各自订阅）
      - 按 seq 去重（每个 session 独立计数）
      - 异步分发（不阻塞 runtime）
    """

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}
        self._last_seq: dict[int, int] = {}  # session_id → last_seq

    # ================================================================
    # 订阅
    # ================================================================

    def subscribe(self, name: str, handler: EventHandler) -> None:
        """
        注册事件处理器

        Args:
            name:    处理器名称（如 "ws", "db", "audit"）
            handler: async callable(session_id, event, payload) → None
        """
        self._handlers[name] = handler
        logger.info("EventBus 订阅: name=%s", name)

    def unsubscribe(self, name: str) -> None:
        self._handlers.pop(name, None)

    # ================================================================
    # 分发
    # ================================================================

    async def dispatch(
        self,
        session_id: int,
        event: str,
        payload: dict = None,
        seq: Optional[int] = None,
    ) -> None:
        """
        分发事件到所有订阅者

        Args:
            session_id: Session ID
            event:      事件类型字符串（如 "agent.step.completed"）
            payload:    事件负载 dict
            seq:        事件序号（用于去重，None 则跳过检查）
        """
        # seq 去重
        if seq is not None:
            last = self._last_seq.get(session_id, 0)
            if seq <= last:
                logger.debug("重复事件已丢弃: session=%d, seq=%d", session_id, seq)
                return
            self._last_seq[session_id] = seq

        payload = payload or {}

        # 异步分发到所有处理器
        for name, handler in self._handlers.items():
            try:
                await handler(session_id, event, payload)
            except Exception as exc:
                logger.error(
                    "EventBus 分发异常: handler=%s, session=%d, event=%s, error=%s",
                    name, session_id, event, exc,
                )

    def reset_seq(self, session_id: int) -> None:
        """重置 Session 的 seq 计数器（Session 重启时调用）"""
        self._last_seq.pop(session_id, None)

    def get_last_seq(self, session_id: int) -> int:
        return self._last_seq.get(session_id, 0)

    def subscriber_count(self) -> int:
        return len(self._handlers)


# ====================================================================
# 全局单例
# ====================================================================

_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
