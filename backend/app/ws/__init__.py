"""
WebSocket 实时推流 — Phase 7 核心模块

Agent 可观测性基础设施：连接管理、心跳、广播、事件协议

模块结构：
  protocol.py:  EventType + WSMessage + 事件工厂函数
  manager.py:   ConnectionManager（按 Session 分组、心跳、广播）
"""
