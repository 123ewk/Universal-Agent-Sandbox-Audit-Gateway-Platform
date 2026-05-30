"""
WebSocketManager — 统一 WebSocket 通信层

职责：
  1. 封装现有 ConnectionManager（app/ws/manager.py）
  2. 自动为每条消息添加 seq 编号（per-session 递增）
  3. 保证消息格式统一：{session_id, seq, event, timestamp, payload}

原则：
  WebSocket 只做通信 — connect/disconnect/broadcast/heartbeat。
  不写业务逻辑。

使用方式：
  wsm = get_websocket_manager()
  await wsm.connect(ws, session_id=42)
  await wsm.broadcast(42, "agent.step.completed", {"step_number": 1})
  await wsm.disconnect(ws, session_id=42)
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    统一 WebSocket 消息管理器

    封装 ConnectionManager，增加：
      - seq 自动编号（每个 session 独立）
      - 统一消息格式保证
    """

    def __init__(self) -> None:
        self._conn = ConnectionManager()
        self._seq: dict[int, int] = {}  # session_id → current_seq

    # ================================================================
    # 连接管理
    # ================================================================

    async def connect(self, ws, session_id: int) -> None:
        await self._conn.connect(ws, session_id)
        logger.info("WS 客户端已连接: session=%d", session_id)

    async def disconnect(self, ws, session_id: int) -> None:
        await self._conn.disconnect(ws, session_id)
        logger.info("WS 客户端已断开: session=%d", session_id)

    async def cleanup_session(self, session_id: int) -> None:
        await self._conn.cleanup_session(session_id)
        self._seq.pop(session_id, None)

    def subscriber_count(self, session_id: int) -> int:
        return self._conn.subscriber_count(session_id)

    def total_connections(self) -> int:
        return self._conn.total_connections()

    # ================================================================
    # 广播
    # ================================================================

    async def broadcast(
        self,
        session_id: int,
        event: str,
        payload: dict = None,
    ) -> int:
        """
        向房间内所有客户端推送统一格式消息

        Args:
            session_id: Session ID
            event:      事件类型字符串（如 "agent.step.completed"）
            payload:    事件负载

        Returns:
            接收到的客户端数量
        """
        # seq 自动递增
        seq = self._seq.get(session_id, 0) + 1
        self._seq[session_id] = seq

        msg = {
            "session_id": session_id,
            "seq": seq,
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload or {},
        }

        sent = await self._conn.broadcast(session_id, msg)
        return sent

    def get_current_seq(self, session_id: int) -> int:
        return self._seq.get(session_id, 0)

    async def broadcast_message(
        self, session_id: int, message: dict
    ) -> int:
        """广播预构建消息（自动添加 seq 编号）"""
        seq = self._seq.get(session_id, 0) + 1
        self._seq[session_id] = seq
        message["seq"] = seq
        return await self._conn.broadcast(session_id, message)


# ====================================================================
# 全局单例
# ====================================================================

_ws_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager
