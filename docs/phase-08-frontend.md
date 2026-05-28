# Phase 8 — Vue 3 前端

## 架构总览

```
WS Event Stream
      │
      ▼
┌─────────────────────────────────────────────────┐
│              Runtime Layer                       │
│                                                   │
│  WSClient → EventBus → Reducers → Pinia Store   │
│  (自动重连)   (seq去重)  (按事件类型分拆)        │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│               Pinia Stores                       │
│                                                   │
│  sessions: Map<sessionId, SessionState>          │
│  approvals: Map<approvalId, ApprovalItem>        │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│             Reactive UI (无框架)                  │
│                                                   │
│  TaskCreate  →  /                                │
│  MonitorView →  /monitor/:id  (StepTimeline)     │
│  HistoryView →  /history     (任务列表)           │
│  ReplayView  →  /replay/:id  (回放 + 截图)       │
│  BrowserPanel + ApprovalDialog + AuditPanel       │
└─────────────────────────────────────────────────┘
```

## 画布流

**Runtime Layer** (P0)：独立于 Vue 组件树之外的事件处理层
- `ws-client.ts` — WebSocket 连接管理，指数退避重连 1s→2s→4s→max30s
- `event-bus.ts` — 事件分发中枢，按 event 前缀路由到不同 reducer
- `reducers/` — 纯状态转换函数：session.reducer / step.reducer / approval.reducer / sandbox.reducer

**Pinia Stores** (P0)：数据缓存层
- `sessions.ts` — `Map<sessionId, SessionState>` 支持多 Session 并行
- `approvals.ts` — 审批请求管理，REST API 操作封装

**UI 组件** (P0/P1/P2)：
- P0: TaskCreate → MonitorView → StepTimeline
- P1: BrowserPanel + ApprovalDialog
- P2: HistoryView + ReplayView + AuditPanel

## 软件架构

```
frontend/
├── index.html / package.json / vite.config.ts    # Vite + Proxy (/api → :8000)
├── src/
│   ├── main.ts                                   # 初始化: Pinia → EventBus → Reducers → WSClient
│   ├── App.vue                                   # Shell (Header + RouterView)
│   ├── router.ts                                 # 4 个页面路由
│   │
│   ├── runtime/                                  # Runtime Layer (独立于 UI)
│   │   ├── event-types.ts                       # 20 种事件类型 + Payload 接口
│   │   ├── ws-client.ts                         # WebSocket 客户端 + 自动重连
│   │   ├── event-bus.ts                         # EventBus (seq 去重 + 前缀路由)
│   │   └── reducers/                            # 4 个 Reducer (session/step/approval/sandbox)
│   │
│   ├── stores/                                  # Pinia 状态管理
│   │   ├── sessions.ts                          # Map<sessionId, SessionState>
│   │   └── approvals.ts                        # 审批请求管理
│   │
│   ├── api/client.ts                            # axios 封装 (任务/审批/截图 API)
│   ├── views/                                   # 4 个页面
│   │   ├── TaskCreate.vue                      # / — 任务创建
│   │   ├── MonitorView.vue                     # /monitor/:id — 实时监控
│   │   ├── HistoryView.vue                     # /history — 任务历史
│   │   └── ReplayView.vue                      # /replay/:id — 步骤回放
│   ├── components/                              # 4 个通用组件
│   │   ├── StepTimeline.vue                    # 步骤时间线
│   │   ├── BrowserPanel.vue                    # 浏览器视图 (URL + 截图)
│   │   ├── ApprovalDialog.vue                  # 审批弹窗 (Teleport to body)
│   │   └── AuditPanel.vue                      # 审计记录面板
│   └── styles/main.css                         # 暗色终端风格
```

## 设计思路

**不是页面系统，是 Agent Runtime Observer。** 前端不直接调用后端 API 驱动业务，而是订阅 WebSocket 事件流，由事件驱动状态变更后 UI 自然响应。

**EventBus → Reducers 替代巨型 if-else。** 每个 Reducer 只处理自己命名空间的事件（agent.* 归 step/session，sandbox.* 归 sandbox，approval.* 归 approval），独立可测试。

**seq 字段去重。** WebSocket 重连后可能收到重复事件，EventBus 维护 `Map<sessionId, lastSeq>`，`seq ≤ lastProcessed` 跳过。

**Map Store 支持多 Session。** `sessions` 使用 `Map<number, SessionState>` 而非单个 currentSession，同时支持实时监控和新标签页回放。

**截图路径 == 步骤时间线。** ReplayView 从截图文件名 `step_{n}_{action}.png` 重建执行序列，不依赖后端额外接口。

**零 UI 框架依赖。** 全部自写 CSS（暗色终端风格），无 Element Plus / Ant Design 依赖。

## 前后端依赖

| 后端端点 | 前端使用场景 |
|---------|------------|
| `POST /api/v1/tasks` | TaskCreate 创建任务 |
| `GET /api/v1/tasks` | HistoryView 列表 |
| `GET /api/v1/tasks/{id}` | MonitorView/ReplayView 查状态 |
| `WS /ws/sessions/{id}` | Runtime Layer 实时事件流 |
| `POST /api/v1/approvals/{id}/approve` | ApprovalDialog 批准 |
| `POST /api/v1/approvals/{id}/deny` | ApprovalDialog 拒绝 |
| `GET /api/screenshots?session_id={id}` | ReplayView 截图列表 |
| `GET /api/screenshots/{file}?session_id={id}` | BrowserPanel/ReplayView 加载图片 |
