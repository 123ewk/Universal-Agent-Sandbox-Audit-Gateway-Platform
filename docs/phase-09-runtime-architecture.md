# Phase 9 — 后端 Runtime 架构重构

## 定位

Phase 1-8 构建了 Agent 的"执行能力"，Phase 9 构建 Agent 的"运行时基础设施"：

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| 启动入口 | 无 main.py，无法 uvicorn | `uvicorn app.main:app --port 8000` |
| 状态管理 | `_active_tasks` 散落各 router | SessionManager 唯一状态源 |
| 事件流 | WS 直连 AgentGraph | EventBus 统一分发 → {WS, DB, Audit} |
| 任务调度 | BackgroundTasks + 手动管理 | TaskManager（asyncio.Task 生命周期） |
| WebSocket | 每个 router 自己管 | WebSocketManager 统一封装 |

## 目标架构

```
POST /api/v1/tasks
        │
        ▼
┌───────────────────┐
│     Router         │  接收请求 → 前置校验 → 返回 202
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│   TaskManager      │  asyncio.create_task(runtime.run())
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│  AgentRuntime      │  封装 AgentGraph.invoke()
│                     │  执行前/中/后 emit 事件
└───────┬───────────┘
        │  emit(event)
        ▼
┌───────────────────┐
│    EventBus        │  顺序 + 去重 + 分发
└───┬───┬───┬───────┘
    │   │   │
    ▼   ▼   ▼
   WS   DB  Audit
```

## 核心模块

### `app/main.py` + `app/app_factory.py`

`create_app()` 只做四件事：middleware + exception + router + lifecycle。不放业务逻辑。

### SessionManager (`app/runtime/session_manager.py`)

唯一的状态管理中心。单例，管理 `Map<session_id, SessionState>` + 每个 Session 的完整 EventLog。提供 `create/get/update/delete` + `add_event/add_step/add_screenshot`。

### TaskManager (`app/runtime/task_manager.py`)

管理 asyncio.Task 生命周期：`start(session_id, coro)` → 创建 Task + 注册 done callback → 更新 SessionManager 状态。支持 `cancel/get_status/list_active`。

### EventBus (`app/runtime/event_bus.py`)

统一事件中枢。支持多订阅者（WS/DB/Audit 各自 subscribe），按 seq 去重，异步分发不阻塞 runtime。

### WebSocketManager (`app/runtime/websocket_manager.py`)

封装现有 ConnectionManager，增加：seq 自动编号（per-session 递增）+ 统一消息格式 `{session_id, seq, event, timestamp, payload}`。提供 `broadcast(session_id, event, payload)` 和 `broadcast_message(session_id, msg)` 两种接口。

### AgentRuntime (`app/runtime/agent_runtime.py`)

封装 AgentGraph.invoke() 为标准化的 Runtime.run()。执行前 emit "agent.started"，执行后 emit "agent.completed"/"agent.failed"，执行完毕后自动 cleanup WebSocket 房间 + 持久化到 PG。

## 统一事件协议

```json
{
  "session_id": 42,
  "seq": 1,
  "event": "agent.step.completed",
  "timestamp": "2026-05-28T12:00:00Z",
  "payload": {}
}
```

`app/ws/protocol.py` 的 WSMessage 模型新增 `seq: Optional[int] = None` 字段（向后兼容）。

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 渐进迁移 | P0 只加文件不改旧代码 | 不破坏 172 个现有测试 |
| DB 导入惰性化 | app_factory 内延迟 import | 缺少 asyncpg 时应用仍可启动（无 DB 模式） |
| seq 去重 | EventBus 层检查 `seq ≤ lastProcessed` | 前端重连后不收到重复事件 |
| 事件持久化 | EventBus db_handler → SessionManager.event_log | 支撑 Replay 功能 |
| done callback | TaskManager 注册回调更新 SessionManager | 任务结束自动同步状态，无需手动管理 |

## 测试

205 passed（172 现有 + 33 新增 runtime 测试），7 skipped（app factory 集成测试需要 asyncpg）。

## 启动

```bash
cd backend
uvicorn app.main:app --reload --port 8000
# → http://localhost:8000/docs  # Swagger
# → http://localhost:8000/api/v1/tasks  # API
```

## 下一步

- 前后端联调：启动后端 → 创建任务 → WS 推流 → 前端展示
- DockerPlaywrightProvider 实现
- Redis 状态持久化（当前仅内存）
