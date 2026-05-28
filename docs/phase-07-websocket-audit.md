# Phase 7 — WebSocket 推流 + 审计审批

## 定位

Phase 1-6 构建了 Agent 的"执行能力"（Engine/Runtime/Sandbox），Phase 7 构建 Agent 的"控制系统"（Control Plane）：

| 维度 | Phase 1-6 | Phase 7 |
|------|-----------|---------|
| 可观测性 | 日志 + 数据库记录 | **实时 WebSocket 事件流** |
| 可控性 | 配置开关 | **Human-in-the-loop 暂停/审批/恢复** |
| 可审计性 | 事后数据库查询 | **跨步骤行为模式检测 + 实时告警** |

这不是"AI 调工具"，而是"企业级 Agent 控制系统"。

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                     Phase 5 AgentGraph                            │
│                                                                    │
│  Plan → Execute → Observe → Reflect                              │
│            │           │         │                                 │
│            │     ┌─────┘         └──────┐                          │
│            ▼     ▼                      ▼                          │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐                  │
│  │ WS Push    │ │ Page Info  │ │ Detector     │                  │
│  │ step_start │ │ from Engine│ │ analyze()    │                  │
│  │ step_done  │ │            │ │ should_pause?│                  │
│  │ approval   │ │            │ │              │                  │
│  └─────┬──────┘ └────────────┘ └──────┬───────┘                  │
│        │                               │                          │
└────────┼───────────────────────────────┼──────────────────────────┘
         │                               │
         ▼                               ▼
┌─────────────────────┐    ┌─────────────────────────┐
│ ConnectionManager   │    │    ApprovalManager       │
│                     │    │                           │
│ • Rooms (per sess)  │    │ request() → asyncio.Event │
│ • broadcast()       │    │ approve() → event.set()  │
│ • heartbeat         │    │ deny() → event.set()     │
│ • auto-cleanup      │    │ timeout → auto-deny       │
└─────────┬───────────┘    └──────────┬──────────────┘
          │                            │
          ▼                            ▼
┌─────────────────────┐    ┌─────────────────────────┐
│   WebSocket 客户端   │    │   REST API 客户端        │
│   实时订阅事件流     │    │   POST /approve/{id}    │
│   前端监控面板       │    │   审批弹窗交互           │
└─────────────────────┘    └─────────────────────────┘
```

## Phase 7 文件结构

```
backend/app/
├── ws/
│   ├── __init__.py       # 模块标记
│   ├── protocol.py       # EventType 枚举 + WSMessage + 事件工厂函数
│   └── manager.py        # ConnectionManager — 按 Session 分组管理连接
├── audit/
│   ├── __init__.py       # 模块标记
│   ├── policies.py       # AuditPolicy — 跨步骤行为检测规则
│   ├── detector.py       # BehaviorDetector — 连接 Policy 与 AgentState
│   ├── approval.py       # ApprovalManager — asyncio.Event 暂停/恢复
│   └── router.py         # FastAPI 审批 REST 端点

修改的文件：
├── agent/graph.py        # Phase 7 集成：WS push + detector + approval
└── agent/router.py       # WS 端点升级为 ConnectionManager 事件推送
```

## 核心概念

### 1. 统一事件协议

**设计动机**：前端通过 WebSocket 接收 Agent 执行事件的实时推送。所有事件使用统一的 JSON 格式，按命名空间分层路由到不同的 UI 组件。

**消息格式**：

```json
{
  "event": "agent.step.completed",
  "session_id": 42,
  "timestamp": "2026-05-28T12:00:00Z",
  "payload": {
    "step_number": 3,
    "skill_name": "browser_click",
    "success": true,
    "execution_time_ms": 150
  }
}
```

**事件命名空间**：

| 命名空间 | 示例事件 | 前端路由到 |
|---------|---------|-----------|
| `agent.*` | `agent.step.completed`, `agent.plan.completed` | AgentPanel |
| `sandbox.*` | `sandbox.navigation`, `sandbox.screenshot` | BrowserView |
| `audit.*` | `audit.risk.detected`, `audit.alert` | AlertBadge |
| `approval.*` | `approval.required`, `approval.approved` | ApprovalDialog |
| `system.*` | `system.heartbeat`, `system.connected` | StatusBar |

**事件工厂函数**：9 个快捷函数（`agent_step_completed()`, `approval_required()`, `audit_risk_detected()`, ...），自动设置 event 类型和 timestamp，消除手动构造 JSON 的错误。

### 2. ConnectionManager — 房间模型

**核心概念**：一个 Agent Session = 一个 WebSocket 房间。多个客户端可以同时订阅同一个 Session（用户 + 审计员 + 系统监控）。

```python
class ConnectionManager:
    _rooms: dict[int, set[WebSocket]]  # session_id → {ws1, ws2, ...}

    async def connect(ws, session_id)     # 加入房间，发送 connected 消息
    async def broadcast(session_id, msg)  # 向房间内所有客户端推送
    async def disconnect(ws, session_id)  # 离开房间
    async def cleanup_session(sid)        # Session 结束时断开所有连接
```

**心跳机制**：每个房间一个独立的 `asyncio.Task`，每 30 秒发送 `system.heartbeat`。发送失败说明客户端已断开，自动清理。房间为空时自动取消心跳任务。

**推送 vs 轮询**：Phase 5 的原始 WS 实现是轮询模式（每秒检查 `_active_tasks` 状态变更）。Phase 7 升级为事件推送模式 — AgentGraph 节点在事件发生时主动调用 `ws_manager.broadcast()`，WS 端点只需保持连接存活。消除了轮询的延迟和 CPU 浪费。

### 3. 跨步骤行为检测 (AuditPolicy)

**与 Phase 4 RiskEngine 的分工**：

| 模块 | 粒度 | 数据来源 | 检测内容 |
|------|------|---------|---------|
| `engine/risk.py` (Phase 4) | 单步 | Skill + params | "这一操作有多危险" |
| `audit/policies.py` (Phase 7) | 跨步骤 | execution_history | "这串行为有多可疑" |

**五条检测规则**：

| 规则 | 触发条件 | 风险分 | 动作 |
|------|---------|--------|------|
| 连续失败 | 连续 ≥3 步失败 | 60 | 建议暂停 |
| 高频重试 | 同一 Skill 在 10s 内执行 ≥5 次 | 50 | 告警 |
| 危险组合 | 访问金融 URL + 提交/支付操作 | 70 | 需要审批 |
| Shell 执行 | 任何 Shell 命令执行 | 85 | 必须审批 |
| 高危 URL | 导航到 admin/internal/dashboard | 65 | 告警 |

**评分体系**：
- 总分 = 各规则触发的最高分（非累加，因为规则之间可能重叠）
- `total_score ≥ 40` → `requires_approval = True`
- `total_score ≥ 60` → `should_pause = True`

### 4. BehaviorDetector — 连接层

**职责**：将 AuditPolicy 的规则分析结果连接到 Agent 运行时。

```python
class BehaviorDetector:
    async def analyze(state, ws_manager) -> PolicyAssessment:
        assessment = self.policies.assess(state.execution_history)
        if assessment.triggers:
            # 通过 WebSocket 推送风险告警
            for trigger in assessment.triggers:
                await ws_manager.broadcast(
                    state.session_id,
                    audit_risk_detected(...)
                )
        return assessment
```

**在 AgentGraph 中的调用位置**：`_reflect_node` 开始时调用。检测到 `should_pause` 时，通过审批管理器暂停 Agent。

### 5. ApprovalManager — asyncio.Event 暂停机制

**核心机制**：

```python
# Agent 端（_execute_node）
req = await approval_mgr.request(session_id, skill_name, risk_score)
# ↑ 内部创建 asyncio.Event，然后 await event.wait()
# 执行被挂起，不占 CPU

# 管理端（REST API）
await approval_mgr.approve(approval_id)
# ↑ 内部调用 event.set()，唤醒 Agent
```

**为什么用 asyncio.Event 而非 while 轮询？** 轮询消耗 CPU 且增加延迟（取决于轮询间隔）。`asyncio.Event` 是操作系统级别的等待机制，挂起时不占 CPU，`set()` 瞬间唤醒。

**超时处理**：`asyncio.wait_for(event.wait(), timeout=300)`，超时后自动标记为 TIMEOUT。

**审批生命周期**：

```
request()
  → status=PENDING, event.wait()
  → approve()  → status=APPROVED,  event.set() → Agent 继续执行
  → deny()     → status=DENIED,    event.set() → Agent 返回错误
  → timeout    → status=TIMEOUT,   自动拒绝    → Agent 返回错误
```

### 6. AgentGraph 集成

**五个集成点**：

```
AgentGraph.invoke()
  ├─ 1. finally: ws_manager.cleanup_session()     ← Session 结束清理房间
  │
  └─ 2. _execute_node:
        ├─ ws_manager.broadcast(step_completed)    ← 每步结果推前端
        └─ approval_mgr.request() + await event    ← 审批暂停
    3. _reflect_node:
        └─ detector.analyze(state, ws_manager)     ← 行为检测 + WS 告警
    4. _observe_node:
        └─ 从 engine.get_page_info() 生成观察     ← 已是 Phase 6
    5. router.py:
        └─ ws_manager.connect/disconnect           ← 升级为事件推送
```

**向后兼容**：所有 Phase 7 组件（ws_manager/detector/approval_manager）均为可选构造参数。未注入时 AgentGraph 以 Phase 5 原有模式运行，不破坏现有逻辑。

## 关键技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 暂停机制 | asyncio.Event（非轮询） | 零 CPU 占用，瞬间唤醒，asyncio 原生支持 |
| 事件转发 | push（非 poll） | 消除轮询延迟（1s→实时），降低 CPU 开销 |
| WS 房间模型 | 每 Session 一房间 | 多客户端可同时订阅，心跳按房间管理 |
| 行为检测位置 | _reflect_node 开始时 | 在评估前检测，可提前暂停避免继续执行 |
| 规则引擎 | 硬编码规则（非 DSL） | 五条核心规则足够，过度设计 DSL 增加复杂度 |
| 协议格式 | 命名空间分层 + Pydantic | `agent.*/sandbox.*/audit.*/approval.*` 前端可按前缀路由 |
| 超时策略 | 拒绝（非自动批准） | 安全优先：宁可误拒不可误放 |
| 审批 API | 混合 REST + WS | REST 用于审批操作（幂等），WS 用于审批通知（即时） |

## 测试覆盖（30 项单元测试）

**TestEventProtocol（9 项）**：
- EventType 枚举；WSMessage 创建和序列化
- make_message（Pydantic model / dict payload）
- heartbeat / connected 快捷函数
- step_completed / approval_required / risk_detected 工厂函数

**TestConnectionManager（5 项）**：
- connect → disconnect 生命周期
- broadcast 向多客户端推送
- 不存在的 Session 返回 0
- active_sessions / cleanup_session

**TestAuditPolicy（7 项）**：
- 空步骤 → 0 分
- 连续 3 次失败检测
- Shell 命令检测
- 危险组合检测（银行 URL + 支付操作）
- 正常步骤不触发
- 高危 URL 检测
- 阈值触发审批

**TestApprovalManager（8 项）**：
- request → approve（并发：Agent 等 + 管理端批）
- request → deny
- request → timeout（1s 超时）
- get_pending / get_pending_by_session
- approve 不存在 → ValueError
- get_history 包含已处理的请求

**TestPhase7Integration（2 项）**：
- detector 无 WS manager 不崩溃
- 完整审批流：request → approve → Agent 恢复

## 八股文 — 面试问答

### Q1: 为什么选择 asyncio.Event 做暂停机制而不是轮询或消息队列？

**答**：三种方案对比：

| 方案 | CPU 占用 | 唤醒延迟 | 复杂度 | 适用场景 |
|------|---------|---------|--------|---------|
| while 轮询 | 持续占用 | 取决于间隔 | 最低 | 不推荐 |
| asyncio.Event | 0（挂起） | <1ms | 低 | 单进程/同线程等待 |
| 消息队列/Redis Pub/Sub | 0 | ~10ms | 高 | 跨进程/跨服务 |

当前场景是单进程 FastAPI 内 Agent 等待 HTTP API 调用，asyncio.Event 是 Python 标准库原生方案，零依赖，零网络开销。Phase 8 如果 Agent 执行引擎独立成微服务，可以迁移到 Redis Pub/Sub，ApprovalManager 的接口不变。

### Q2: ConnectionManager 的心跳为什么是 30 秒而不是 TCP keepalive？

**答**：TCP keepalive 默认 2 小时，即使调整到秒级也只检测 TCP 连接状态，不检测应用层（如浏览器标签页被挂起）。30 秒心跳是应用层协议 — 发送 `system.heartbeat` 消息，发送失败 = 客户端已不可达。心跳还兼做冷却检测：如果 30 秒内没有任何 Agent 事件，心跳告诉前端"连接还活着"。

每个房间一个独立心跳任务，房间为空时自动取消，不会泄漏。

### Q3: AuditPolicy 的五条规则是"硬编码"的，为什么不用配置驱动或 DSL？

**答**：三条原因：

1. **规则数量有限** — 当前只有五条核心规则，硬编码比解析配置文件更直接。当规则增长到 20+ 条时可以考虑 YAML/JSON 配置。
2. **规则需要上下文** — 每条规则不仅需要参数（阈值、窗口），还需要访问 Python 对象（StepRecord 的属性）。配置驱动需要一套表达式引擎，当前阶段不值得。
3. **测试性** — 硬编码规则可以直接在单测中修改类属性（如 `policy.APPROVAL_THRESHOLD = 20`），无需模拟配置文件加载。

Phase 8 如果需求明确（如不同租户不同策略），可以提取 `PolicyConfig` dataclass，从数据库或配置文件加载。

### Q4: `BehaviorDetector.analyze()` 为什么在 `_reflect_node` 而不是 `_execute_node` 调用？

**答**：`_execute_node` 的职责是"执行当前步骤"，`_reflect_node` 的职责是"评估已完成的步骤序列"。行为检测需要分析**执行历史**（多步模式），自然属于 Reflect 阶段。

如果在 Execute 中检测，每步只能看到单步数据，无法检测"连续失败""危险组合"等跨步骤模式。放在 Reflect 中，`execution_history` 已包含最新步骤，检测结果可以影响路由决策（continue/replan/abort）。

### Q5: 审批超时为什么是"拒绝"而不是"自动批准"？

**答**：安全优先原则。审批的存在本身就说明这是高危操作。如果超时自动批准，就等于在审批人不在时开了一个时间窗口的安全漏洞。超时拒绝意味着：**无人审批 = 不允许执行**。Agent 收到 TIMEOUT 后会走 abort 路径，任务标记为失败，不会继续。

如果某些场景需要"超时自动批准"（如内部测试环境），可以通过 `ApprovalManager(timeout_seconds=N)` 和 `deny_on_timeout=False` 配置，但默认行为必须是拒绝。

### Q6: WebSocket 消息格式为什么用字符串 event 类型而非数字 code？

**答**：数字 code（如 HTTP 状态码）需要一份 code↔含义的映射表，增加维护成本。字符串 `"agent.step.completed"` 自描述，日志中不需要查表。命名空间分层（`agent.*` / `sandbox.*` / `audit.*` / `approval.*`）让前端可以按前缀订阅 — 例如 `subscribe("audit.*")` 接收所有审计相关事件。

这正是 WebSocket 和 REST 的区别：REST 用数字状态码（标准、机器可读），WS 事件类型用字符串（自描述、开发者可读）。

### Q7: Phase 5 的原 WS 实现（轮询模式）和 Phase 7 的 ConnectionManager（推送模式）本质区别是什么？

**答**：Phase 5 的 WS 端点是**数据拉取** — 每秒检查 `_active_tasks` 字典，对比 `last_step_count` 判断是否有新步骤。这是把 WebSocket 当成"快轮询"在用。

Phase 7 的 ConnectionManager 是**事件推送** — AgentGraph 节点在事件发生时主动调用 `broadcast()`。WS 端点只需 `connect()` + `await sleep(30)` 保持连接存活。本质区别：Phase 5 是"客户端问服务器有没有新数据"，Phase 7 是"服务器有数据就推给客户端"。

推送模式消除了 1 秒轮询延迟，CPU 从持续忙等降为零，且不会漏事件（轮询模式下快速执行两步可能只检测到一次变更）。

### Q8: `_route_after_reflect` 中如何处理检测器触发的 `should_pause`？

**答**：当前实现中，`should_pause` 由 `BehaviorDetector.analyze()` 返回，但暂停动作在 `_execute_node` 的审批等待中执行（`await approval_mgr.request().event.wait()`）。两个节点配合：

1. `_reflect_node`：检测到 `should_pause` → 标记 `state.needs_approval`
2. `_route_after_reflect`：正常返回 "execute"（不阻塞）
3. `_execute_node`：检查 `needs_approval` → 调用 `approval_mgr.request()` → `await event.wait()`
4. 外部 API 调用 approve/deny → `event.set()` → Agent 恢复

暂停发生在 Execute 而非 Reflect，因为 Execute 是唯一"执行操作"的节点 — 暂停应该在操作执行前。

## 与前后 Phase 的关系

```
Phase 4: Skill Runtime + RiskEngine
  ↓  每个 Skill 调用都经过 RiskEngine.assess()
Phase 5: AgentGraph (Plan-Execute-Observe-Reflect)
  ↓  Agent 执行循环
Phase 6: Playwright Sandbox
  ↓  真实浏览器操作
Phase 7: WebSocket + Audit (← 当前)
  ↓  实时推送 + 行为检测 + 审批暂停
Phase 8: Vue 3 前端
  ↓  消费 WS 事件 + 审批交互
```
