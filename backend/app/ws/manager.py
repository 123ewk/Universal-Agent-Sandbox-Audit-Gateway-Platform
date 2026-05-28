"""
ConnectionManager — WebSocket 连接管理器

设计动机：
  多客户端可能同时订阅同一个 Agent Session 的执行事件。
  ConnectionManager 管理 WebSocket 连接的生命周期，
  支持按 Session 分组广播、心跳检测、自动清理断开连接。

核心概念：Room = Session
  每个 Agent Session 是一个"房间"，订阅该 Session 的所有 WS 客户端
  都会收到该 Session 的事件推送。客户端断开不影响 Session 继续执行。

使用方式：
  manager = ConnectionManager()
  await manager.connect(websocket, session_id)
  await manager.broadcast(session_id, message)
  await manager.disconnect(websocket, session_id)
"""
import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from app.ws.protocol import heartbeat, make_message, EventType

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    WebSocket 连接管理器

    按 Session ID 分组管理连接。一个 Session 可以有多个订阅者
    （如：用户自己 + 审计员 + 系统监控）。

    线程安全：单线程 asyncio 环境下不需要锁。
    """

    def __init__(self, heartbeat_interval: int = 30) -> None:
        # session_id → set of WebSocket connections
        self._rooms: dict[int, set[WebSocket]] = {}
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_tasks: dict[int, asyncio.Task] = {}

    # ================================================================
    # 连接管理
    # ================================================================

    async def connect(self, websocket: WebSocket, session_id: int) -> None:
        """
        接受 WebSocket 连接并加入指定 Session 房间

        Args:
            websocket:  FastAPI WebSocket 实例
            session_id: Agent Session ID
        """
        await websocket.accept()

        if session_id not in self._rooms:
            self._rooms[session_id] = set()

        self._rooms[session_id].add(websocket)

        # 发送连接确认
        await websocket.send_json(
            make_message(EventType.SYSTEM_CONNECTED, session_id, {
                "message": "已连接到 Session",
                "subscriber_count": self.subscriber_count(session_id),
            }).to_dict()
        )

        logger.info(
            "WS 客户端已连接: session=%d, 当前订阅者=%d",
            session_id, self.subscriber_count(session_id),
        )

        # 启动心跳（每个 Session 一个心跳任务）
        if session_id not in self._heartbeat_tasks:
            self._heartbeat_tasks[session_id] = asyncio.create_task(
                self._heartbeat_loop(session_id)
            )

    async def disconnect(self, websocket: WebSocket, session_id: int) -> None:
        """
        断开 WebSocket 连接，从房间中移除

        Args:
            websocket:  FastAPI WebSocket 实例
            session_id: Agent Session ID
        """
        if session_id in self._rooms:
            self._rooms[session_id].discard(websocket)
            remaining = len(self._rooms[session_id])

            if remaining == 0:
                del self._rooms[session_id]
                # 取消心跳
                task = self._heartbeat_tasks.pop(session_id, None)
                if task:
                    task.cancel()
                logger.info("Session %d 房间已清空，心跳已停止", session_id)
            else:
                logger.info(
                    "WS 客户端已断开: session=%d, 剩余订阅者=%d",
                    session_id, remaining,
                )

    # ================================================================
    # 消息广播
    # ================================================================

    async def broadcast(
        self,
        session_id: int,
        message: Any,
    ) -> int:
        """
        向指定 Session 的所有订阅者广播消息

        Args:
            session_id: 目标 Session ID
            message:    消息（dict 或 WSMessage）

        Returns:
            成功发送的客户端数量
        """
        if session_id not in self._rooms:
            return 0

        data = message.to_dict() if hasattr(message, "to_dict") else message
        disconnected: list[WebSocket] = []
        sent_count = 0

        for ws in self._rooms[session_id]:
            try:
                await ws.send_json(data)
                sent_count += 1
            except Exception:
                disconnected.append(ws)
                logger.warning("WS 发送失败，标记为断开: session=%d", session_id)

        # 清理断开的连接
        for ws in disconnected:
            await self.disconnect(ws, session_id)

        return sent_count

    async def broadcast_all(self, message: Any) -> int:
        """向所有 Session 的所有订阅者广播"""
        total = 0
        for session_id in list(self._rooms.keys()):
            total += await self.broadcast(session_id, message)
        return total

    # ================================================================
    # 查询
    # ================================================================

    def subscriber_count(self, session_id: int) -> int:
        """获取指定 Session 的订阅者数量"""
        if session_id in self._rooms:
            return len(self._rooms[session_id])
        return 0

    def active_sessions(self) -> list[int]:
        """返回有活跃订阅者的 Session ID 列表"""
        return list(self._rooms.keys())

    def total_connections(self) -> int:
        """返回所有活跃连接总数"""
        return sum(len(room) for room in self._rooms.values())

    # ================================================================
    # 心跳
    # ================================================================

    async def _heartbeat_loop(self, session_id: int) -> None:
        """
        定期发送心跳，检测断开的连接

        心跳间隔由 heartbeat_interval 控制（默认 30s）。
        """
        try:
            while session_id in self._rooms:
                await asyncio.sleep(self._heartbeat_interval)
                if session_id not in self._rooms:
                    break
                count = await self.broadcast(session_id, heartbeat(session_id))
                if count > 0:
                    logger.debug("心跳: session=%d, 订阅者=%d", session_id, count)
        except asyncio.CancelledError:
            logger.debug("心跳已取消: session=%d", session_id)

    # ================================================================
    # 清理
    # ================================================================

    async def cleanup_session(self, session_id: int) -> None:
        """
        Session 结束时的清理

        发送完成消息后断开所有连接。
        """
        if session_id in self._rooms:
            # 通知所有订阅者
            await self.broadcast(
                session_id,
                make_message(EventType.AGENT_COMPLETED, session_id, {
                    "message": "Session 已结束",
                }),
            )

            # 关闭所有连接
            for ws in list(self._rooms.get(session_id, set())):
                try:
                    await ws.close()
                except Exception:
                    pass

            self._rooms.pop(session_id, None)

        # 取消心跳
        task = self._heartbeat_tasks.pop(session_id, None)
        if task:
            task.cancel()
