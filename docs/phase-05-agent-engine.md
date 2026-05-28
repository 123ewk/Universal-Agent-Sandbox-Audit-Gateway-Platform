# Phase 5 — LangGraph Agent 编排引擎

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                      用户输入任务                                 │
│          POST /api/v1/tasks {"task_description": "..."}          │
└──────────────┬───────────────────────────────────────────────────┘
               │ 202 Accepted + task_id
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FastAPI Router (agent/router.py)               │
│                                                                    │
│  1. 创建 AgentSession (DB)                                        │
│  2. BackgroundTasks 启动 agent_graph.invoke()                     │
│  3. WebSocket 实时推送执行事件 (/ws/sessions/{id})                │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  AgentGraph (agent/graph.py)                      │
│                  LangGraph StateGraph                             │
│                                                                    │
│    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐     │
│    │  PLAN   │───▶│ EXECUTE │───▶│ OBSERVE │───▶│ REFLECT │     │
│    │ LLM拆解 │    │ Gateway │    │ 流水线  │    │ LLM评估 │     │
│    │ 任务为  │    │ 安全执行│    │ 压缩    │    │ 决策    │     │
│    │ 步骤列表│    │ Skill   │    │ 观察值  │    │ 下一步  │     │
│    └─────────┘    └─────────┘    └─────────┘    └────┬────┘     │
│         ▲                                            │          │
│         │          ┌─────────────────────────────────┘          │
│         │          │  continue/retry → EXECUTE                  │
│         │          │  replan        → PLAN                      │
│         └──────────┤  complete/abort → END                      │
│                    └────────────────────────────────────────────│
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│               七层上下文管理器 (agent/context.py)                  │
│                                                                    │
│  L7: System    (固定 ~300t)  ─ 身份与安全规则                      │
│  L6: Task      (固定 ~500t)  ─ 任务目标 + 执行计划                 │
│  L5: Execution (固定  ~50t)  ─ 进度指示                            │
│  L4: Observation(~200t)     ─ 结构化观察摘要（增量更新）           │
│  L3: Working   (~4000t)     ─ 最近3-5步详细记录（动态裁剪）        │
│  L2: Memory    ( ~800t)     ─ 向量检索的长期记忆                   │
│  L1: Tool      ( ~400t)     ─ 可用Skill schema（轻量前置）         │
│  L0: Tool Doc  (~1200t)     ─ 被选中Skill的skill.md（按需加载）    │
└──────────────────────────────────────────────────────────────────┘
```

## Phase 5 文件结构

```
backend/app/
├── models/
│   └── memory.py            # pgvector 向量记忆模型（NEW）
├── agent/
│   ├── __init__.py           # 模块标记
│   ├── state.py              # AgentState + StepRecord + PlanStep（Pydantic）
│   ├── context.py            # ContextManager — 七层上下文组装
│   ├── compression.py        # ContextCompressor — Working裁剪 + 增量摘要
│   ├── observation.py        # ObservationPipeline — DOM→摘要四阶段流水线
│   ├── prompts.py            # SystemPrompt / PlanPrompt / ReflectPrompt 模板
│   ├── llm.py                # LLMClient — 多模型工厂（OpenAI/DeepSeek/Claude）
│   ├── graph.py              # AgentGraph — LangGraph StateGraph 编排
│   └── router.py             # FastAPI REST + WebSocket 端点
tests/
├── test_agent_state.py       # 31 项 — 状态模型测试
├── test_agent_context.py     # 14 项 — 七层上下文组装测试
├── test_agent_observation.py # 23 项 — 观察流水线测试
├── test_agent_compression.py # 13 项 — 压缩器测试
└── test_agent_prompts.py     # 20 项 — Prompt模板测试
```

## 核心概念

### 1. Plan-Execute-Observe-Reflect 循环

**设计动机**：ReAct 模式（Reason+Act）每一步都让 LLM 决定"下一步做什么"，容易陷入无限循环且 Token 消耗高。Plan-Execute-Reflect 先规划全貌再逐步执行，每步只需判断"这一步成功了吗"，大幅减少 LLM 调用。

**四个节点**：

| 节点 | 职责 | LLM 调用 | 输出 |
|------|------|---------|------|
| **Plan** | 将用户任务拆解为 Skill 调用步骤列表 | 是 | `list[PlanStep]` JSON |
| **Execute** | 通过 AuditGateway 安全执行当前步骤 | 否 | `StepRecord` |
| **Observe** | ObservationPipeline 处理执行结果，压缩为结构化摘要 | 否 | `ObservationRecord` |
| **Reflect** | 评估执行结果，决定 continue/retry/replan/complete/abort | 是（部分自动） | 决策 JSON |

**LangGraph 实现**：

```python
workflow = StateGraph(AgentState)
workflow.add_node("plan", self._plan_node)
workflow.add_node("execute", self._execute_node)
workflow.add_node("observe", self._observe_node)
workflow.add_node("reflect", self._reflect_node)

workflow.set_entry_point("plan")
workflow.add_edge("plan", "execute")
workflow.add_edge("execute", "observe")
workflow.add_edge("observe", "reflect")

# 条件边：Reflect 的五个出口
workflow.add_conditional_edges(
    "reflect",
    self._route_after_reflect,
    {"execute": "execute", "replan": "plan", "end": END},
)
```

**条件路由逻辑**：

```python
def _route_after_reflect(state):
    if state.is_finished:       # COMPLETED / FAILED / CANCELLED
        return "end"
    if state.needs_replan:      # Reflect 决定重新规划
        return "replan"
    if remaining_steps:         # 还有未完成的步骤
        return "execute"
    return "end"
```

**Reflect 自动决策（不调 LLM）**：
- 等待审批 → 暂停（开发环境自动通过）
- 最后一步且成功 → complete
- 连续 3 次失败 → replan（不可恢复 → abort）

### 2. 七层上下文管理 (ContextManager)

**设计动机**：LLM 上下文窗口很贵（每 1000 token 都是成本），传统做法直接把所有状态塞进 Prompt，原始 HTML、完整日志、大段 JSON 迅速耗尽上下文窗口。七层架构让**系统决定什么进上下文，不是 LLM 决定**。

**核心铁律**：原始数据绝不进 Prompt — HTML/日志/大JSON → 外部存储 → 摘要 + 引用。

**七层分层与预算**（默认 8000 tokens ≈ 32000 chars）：

| 层 | 优先级 | 预算 | 内容 | 策略 |
|----|--------|------|------|------|
| System | 7 (最高) | 300t | 身份与安全规则 | 始终加载 |
| Task | 6 | 500t | 任务目标 + 计划步骤 | 始终加载 |
| Execution | 5 | 50t | 步骤进度 | 始终加载 |
| Observation | 4 | 200t | 结构化观察摘要 | 增量更新 |
| Working | 3 | 4000t | 最近步骤详细记录 | 动态裁剪 |
| Memory | 2 | 800t | 向量检索的长期记忆 | 按需注入 |
| Tool Schema | 1 | 400t | 可用 Skill 列表 | 始终加载 |
| Tool Doc | 0 (最低) | 1200t | 被选中 Skill 的 skill.md | 按需加载 |

**组装算法**：按优先级降序逐层累加，超出总预算时从低优先级层截断。

```python
def assemble(state, selector, system_prompt, skill_doc_name):
    layers = [
        ("system",     system_prompt,         7),   # 最高优先级
        ("task",       build_task(state),     6),
        ("execution",  build_execution(state),5),
        ("observation",build_observation(state),4),
        ("working",    build_working(state),  2),   # 最大块
        ("memory",     build_memory(state),   3),
        ("tool_schema",build_tool_schema(sel), 1),
        ("tool_doc",   build_tool_doc(sel, doc),0), # 最低优先级
    ]
    layers.sort(key=lambda x: x[2], reverse=True)
    # 按优先级累加，超出 max_chars 截断
    ...
```

### 3. 观察值处理流水线 (ObservationPipeline)

**四阶段流水线**：`Browser DOM → Noise Filter → UI Parser → Summarizer → Structured Observation`

**Stage 1 — Noise Filter（噪声过滤器）**：
- 删除完整标签：script, style, noscript, iframe, svg, canvas, video, audio
- 删除噪声 class/id：advertisement, tracking, analytics, cookie-banner, gdpr, popup, hidden, sr-only
- 删除 HTML 注释、空白行、多余空格

**Stage 2 — UI Parser（交互元素提取器）**：
- 保留标签：button, input, select, textarea, form, a, table, h1-h6, nav, menu
- 提取 button 的 text + selector
- 提取 input 的 name/id/type/placeholder → 生成 selector
- 提取链接的 href + 文本（过滤 javascript:# 空链接）
- 输出格式：`[{type: "button", text: "搜索", selector: "#btn"}, ...]`

**Stage 3 — Summarizer（摘要生成器）**：

生成一句话自然语言摘要，不求优美、求信息密度：
```
页面 '百度一下'，包含 3 个按钮、12 个链接、1 个输入框
```

**Stage 4 — 结构化组装 (ObservationRecord)**：

```python
ObservationRecord(
    summary="页面 '百度一下'，包含 3 个按钮、12 个链接、1 个输入框",
    page_title="百度一下",
    page_url="https://www.baidu.com",
    interactive_elements=[...],   # 最多 20 个元素
    errors=["404 页面不存在"],     # 最多 3 条
    warnings=["页面需要 JavaScript"],
    raw_data_ref="/tmp/raw_page_001.html",  # 原始数据引用，不存内容
)
```

**错误/警告自动检测**：
- 错误关键词：error, exception, 404, 500, 403, access denied, forbidden, captcha
- 警告关键词：timeout, rate limit, JavaScript required, 空页面

### 4. 上下文裁剪与压缩 (ContextCompressor)

**两种裁剪策略**：

| 策略 | 触发条件 | 行为 |
|------|---------|------|
| Soft Trim | 步数 > 5 | 保留最近 5 步，裁剪更早的 |
| Hard Trim | Token 估计 > 4000 | 递减保留步数直到不超限（最低 3 步） |

**裁剪后的步骤处理**：
- 提取为简洁摘要追加到 `observation_summary`（增量不重算）
- 失败的步骤标记 `_persist_to_memory: True`（高优先级持久化）
- 涉及审批的步骤标记 `_persist_to_memory: True`（合规需求）
- 普通成功步骤不持久化（减少向量噪音）

**增量摘要更新**（性能关键优化）：

```python
def update_summary(state, step, observation_summary):
    """每步执行后追加一行，不重新总结所有步骤"""
    line = f"[S{step.step_number}] {step.skill_name}: {'OK' if step.success else 'ERR'}"
    state.observation_summary += "\n" + line
```

### 5. LLM 多模型工厂 (LLMClient)

**支持三种后端**：

| Provider | 模型列表 | API 格式 | SDK |
|----------|---------|---------|-----|
| DeepSeek | deepseek-chat, deepseek-reasoner, deepseek-v4-flash | OpenAI 兼容 | langchain-openai |
| OpenAI | gpt-4o, gpt-4o-mini | OpenAI 原生 | langchain-openai |
| Claude | claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5 | Anthropic | langchain-anthropic |

**统一响应模型 (LLMResponse)**：

```python
@dataclass
class LLMResponse:
    content: str                 # 文本回复
    tool_calls: list[dict]       # Function calls
    model: str                   # 实际使用的模型名
    tokens_used: int             # Token 消耗
    cost: Decimal                # 费用（USD）
```

**自动费用计算**：根据模型定价表 × Token 用量计算，不依赖 LLM API 返回的 usage 信息。

**Plan/Reflect 专用接口**：`llm.plan()` 自动解析 JSON 步骤列表，`llm.reflect()` 自动解析决策 JSON。

### 6. Agent 状态模型 (AgentState)

**核心设计**：Pydantic BaseModel 作为 LangGraph 共享状态，所有节点读写同一实例。

```python
class AgentState(BaseModel):
    # Task
    session_id: int
    task_description: str
    agent_status: AgentStatus   # IDLE→PLANNING→EXECUTING→OBSERVING→REFLECTING→COMPLETED

    # Plan
    plan_steps: list[PlanStep]  # LLM 生成的步骤列表
    current_step_index: int

    # Execution
    execution_history: list[StepRecord]
    total_steps_executed: int

    # Observation
    observation_summary: str    # 增量更新
    last_observation: ObservationRecord

    # Memory
    memory_context: str         # 向量检索的文本

    # Cost
    total_llm_cost: Decimal
    total_tokens_used: int

    # Routing
    needs_replan: bool          # Reflect → Plan 的信号
```

**状态转换规则**：

| 当前状态 | 允许转换到 |
|---------|-----------|
| IDLE | PLANNING |
| PLANNING | EXECUTING, FAILED |
| EXECUTING | OBSERVING, WAITING_APPROVAL, FAILED |
| OBSERVING | REFLECTING, EXECUTING |
| REFLECTING | EXECUTING, COMPLETED, FAILED |
| WAITING_APPROVAL | EXECUTING, CANCELLED |

### 7. pgvector 向量记忆 (MemoryVector)

**设计决策**：不引入独立向量数据库（Pinecone/Weaviate/Qdrant），使用 PostgreSQL pgvector 扩展，与业务数据天然可 JOIN，降低运维复杂度。

**表结构**：

```sql
CREATE TABLE memory_vectors (
    id            SERIAL PRIMARY KEY,
    session_id    INTEGER REFERENCES agent_sessions(id) ON DELETE CASCADE,
    step_id       INTEGER,
    memory_type   VARCHAR(32),       -- observation/decision/error/correction/reflection
    content       TEXT,              -- 原始文本（检索后注入 LLM）
    embedding     VECTOR(1536),      -- 向量嵌入（text-embedding-3-small 维度）
    metadata      JSONB,             -- 结构化元数据（skill_name, url, error_code...）
    access_count  INTEGER DEFAULT 0, -- 访问计数（用于重要性加权）
    created_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ
);

-- ivfflat 索引（余弦距离，百万级以下数据量适用，召回率 > 95%）
CREATE INDEX ix_memory_embedding_ivfflat
    ON memory_vectors
    USING ivfflat (embedding vector_cosine_ops);
```

**记忆持久化策略**：
- 失败步骤 → `memory_type=error`，高优先级持久化（供后续参考）
- 审批步骤 → `memory_type=decision`，持久化（合规需求）
- 普通成功步骤 → 不持久化（减少向量噪音）
- 反射评估 → `memory_type=reflection`，按需持久化

**访问计数**：每次检索命中时 `access_count += 1`，用于重要性加权（被反复引用的记忆更可能被保留）。

## Prompt 模板（按模型定制）

### System Prompt 示例（DeepSeek）

```
你是一个浏览器自动化 Agent，运行在安全沙箱 ShadowOS 中。
你的能力：通过调用 Tool 操作浏览器、读写文件、执行命令。
你的约束：
1. 每次只执行一个 Tool 调用，等待观察结果后再继续
2. 不得尝试访问系统敏感路径（/etc/、/root/、.ssh/）
3. 遇到审批要求时等待人类确认，不得自行绕过
4. 原始 HTML/日志不进入上下文，你会收到结构化摘要
5. 如果步骤连续失败 3 次，应主动要求 replan
```

### Plan Prompt 结构

```
你是一个任务规划器。请将用户的任务分解为具体的执行步骤。

## 用户任务
{task}

## 可用工具
{tools}

## 历史上下文
{history}

## 规划规则
1. 每个步骤必须使用一个具体的 Tool
2. 步骤之间的数据依赖要明确
3. 按 Tool 的 Tier 分级规划：先 CORE（导航/截图），再 INTERACTION（点击/输入）
4. 预计总步数不要超过 10 步

## 输出格式
请以 JSON 数组格式输出执行计划...
```

### Reflect Prompt 结构

```
你是一个执行评估器。评估刚才执行的步骤结果，并决定下一步动作。

## 任务目标
{task}

## 刚执行的步骤
{step_info}

## 剩余步骤
{remaining_steps}

## 评估规则
1. 步骤成功 + 还有剩余步骤 → continue
2. 步骤成功 + 无剩余步骤 → complete
3. 步骤失败 + 可重试 → retry（最多 2 次）
4. 步骤失败 + 当前计划不可行 → replan
5. 检测到安全风险/无法继续 → abort
```

**三层 Prompt 适配**：DeepSeek（简洁中文）、OpenAI（Markdown英文）、Claude（安全边界强调）。三个 Prompt 的核心结构相同，语言和风格按模型偏好定制。

## 关键技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Agent 循环模式 | Plan-Execute-Reflect（非 ReAct） | ReAct 每步都调 LLM 决定 next action，Token 消耗高且易跑偏；P-E-R 先规划后逐步执行，每步只评估此步成败 |
| 上下文架构 | 七层分层 + 优先级预算 | 系统决定什么进上下文，不是 LLM 决定；预算制确保不超窗口 |
| 原始数据策略 | 不进 Prompt | HTML/日志/大JSON → 外部存储 → 摘要+引用路径 |
| Observation 处理 | 规则流水线（非 LLM 摘要） | 确定性、零延迟、零费用；LLM 只对最终结构化摘要进行推理 |
| 向量记忆 | PostgreSQL pgvector（非独立向量 DB） | 与业务数据天然 JOIN，降低运维复杂度 |
| 摘要更新 | 增量追加（非全量重算） | 每次重算 O(n) → O(1)，性能关键优化 |
| LangGraph 状态 | Pydantic BaseModel（非 TypedDict） | 类型验证、自动序列化、IDE 补全、与 FastAPI 统一 |
| Working 裁剪 | 保留最近 3-5 步 | 兼顾上下文完整性和预算控制 |
| 状态路由 | field + 纯函数（非 _decision 临时字段） | Pydantic 不支持临时字段，needs_replan 是显式状态 |
| 多模型支持 | langchain ChatOpenAI/ChatAnthropic | 统一接口，切换模型只需改配置 |
| Skill 文档注入 | 两层信息模型（schema前置 + skill.md按需） | Phase 4 设计，Phase 5 Agent 层原生适配 |

## 测试覆盖（101 项单元测试）

**TestAgentState（31 项）**：
- AgentStatus 枚举完整性
- PlanStep 创建 + 默认值 + step_number 校验
- StepRecord 创建 + duration_seconds + is_completed + error
- ObservationRecord 空/有数据/有错误
- AgentState 初始化 + max_steps 校验 + is_finished + progress_pct
- recent_steps / current_plan_step / last_step / steps_remaining
- record_step / add_cost / transition_to（含非法转换检测）

**TestContextManager（14 项）**：
- 初始化 + estimate_tokens（英文/中文/空）
- assemble 各层验证（System/Task/Execution/Observation/Working/Memory/Tool）
- skill_doc 按需加载 + 自定义 system_prompt
- 预算截断（小窗口不崩溃）
- 空工具列表不崩溃

**TestObservationPipeline（23 项）**：
- Noise Filter: 删除 script/style/iframe/ads/cookie-banner + 保留 button/input
- UI Parser: 提取 button/input/link/heading + 交互元素去重
- Summarizer: 有元素/空页面
- 错误检测: 404/access denied/captcha/timeout/空页面
- 异步 process: 完整流水线/空HTML/带按钮HTML/噪声过滤/raw_data_ref

**TestContextCompressor（13 项）**：
- 初始化 + Token估算（空/基础）
- 不超限不裁剪 + 超步数裁剪 + 生成摘要
- 摘要格式（成功/失败）+ 长期记忆标记（失败/审批/成功不标记）
- 增量摘要更新（单步/累加/含错误）

**TestPromptBuilder（20 项）**：
- 三种 provider 初始化 + 未知 fallback
- System Prompt 三套语言
- Plan Prompt 模板（空/有历史）
- Execute Prompt（基本/有观察/无当前步骤/带 skill_doc）
- Reflect Prompt（基本/无上一步/含错误）
- 工具列表格式化（空/有工具）+ 历史格式化

## 八股文 — 面试问答

### Q1: 为什么用 Plan-Execute-Reflect 而不是 ReAct？

**答**：ReAct（Reasoning + Acting）在每一步都让 LLM 决定"下一步做什么"，这导致三个问题：(1) 每步都消耗一次 LLM 调用，Token 成本高；(2) 缺乏全局视角，LLM 容易陷入局部最优甚至无限循环；(3) 执行进度难以追踪。

Plan-Execute-Reflect 先做全局规划，将任务拆解为明确的步骤列表（Plan），然后逐步执行（Execute），每步执行后观察结果（Observe），最后评估是否继续（Reflect）。每一步的评估只需判断"这一步成功了吗"，不需要重新理解整个任务。Reflect 的大部分决策是自动的（最后一步成功 → complete，连续失败 → replan），只有模糊情况才调用 LLM。这样减少了 50%-70% 的 LLM 调用次数。

### Q2: 七层上下文的"优先级预算"机制是怎么工作的？

**答**：传统做法是把所有内容平铺在一个 Prompt 里，LLM 自己决定关注什么。但 LLM 的注意力是不可控的，它可能忽略关键的安全规则，却花 Token 去理解一段广告 HTML。

我们的方案是：系统决定什么进上下文，不是 LLM 决定。七层各有优先级和预算上限（System=7最高, Tool Doc=0最低），`assemble()` 按优先级降序逐层累加，当总字符数超出 `max_chars` 时从低优先级层截断。这保证了最重要的内容（安全规则、任务目标、执行进度）始终在上下文中，而最不重要的（skill.md 详细文档）可以被截断。组装算法是确定性的，不依赖 LLM 的注意力。

### Q3: 为什么观察值处理用规则流水线而不是 LLM 摘要？

**答**：三个原因：(1) **确定性** — 规则流水线的输出是可预测、可测试的，LLM 摘要的质量不稳定；(2) **成本** — 流水线零 API 费用，如果每步都让 LLM 摘要 HTML，成本翻倍；(3) **延迟** — 流水线毫秒级完成，LLM 调用秒级延迟。

规则流水线（Noise Filter → UI Parser → Summarizer）虽然是正则表达式级别的处理，但对于 HTML 噪声过滤和交互元素提取这个特定任务来说已经足够精准。LLM 的角色是接收结构化 ObservationRecord 后进行高层次推理，而不是做文本清理。

### Q4: ContextCompressor 的增量摘要和全量重算有什么区别？

**答**：全量重算每次遍历 `execution_history` 里的所有 StepRecord 重新生成摘要，时间复杂度 O(n)，Agent 执行 50 步后每次重算都很昂贵。增量更新 `update_summary()` 只追加一行新摘要，O(1) 操作。

当历史步骤被裁剪时（超出 5 步的旧步骤），`compress()` 将这些被裁剪的步骤批量生成一次摘要追加到 `observation_summary`。这样保证 `observation_summary` 始终反映全貌，但更新成本极低。

### Q5: `pgvector` 和独立向量数据库（Pinecone/Weaviate）的取舍逻辑？

**答**：对于 ShadowOS 的场景（每个 Session 几十到几百条记忆，总量百万级以下），独立向量数据库是过度设计：

- **pgvector 优势**：零运维成本（与 PG 共用），向量与业务数据天然 JOIN（如 "查某 session 的所有记忆"），ivfflat 索引百万级以下召回率 >95%
- **独立向量 DB 优势**：亿级以上数据、高并发检索、HNSW 索引更快

当前阶段 pgvector 完全够用。如果未来需要扩展到亿级记忆量，架构上 PG → Weaviate/Qdrant 的迁移成本不高，因为 MemoryVector 的 content+metadata 结构与向量 DB 的 document 模型天然对应。

### Q6: AgentState 为什么用 Pydantic BaseModel 而不是 TypedDict？

**答**：LangGraph 原生支持两者，但 Pydantic 提供：(1) 类型验证（max_steps 不能为 0，非法状态转换抛异常）；(2) `@property` 计算属性（progress_pct, is_finished, recent_steps）；(3) `@field_validator` 输入校验；(4) IDE 类型补全；(5) 与 FastAPI 的 Pydantic 生态统一。

TypedDict 更轻量但不提供验证，在状态复杂的 Agent 场景下容易出现数据不一致的 bug。Pydantic 的序列化/反序列化也与 LangGraph 的 checkpoint 机制集成良好。

### Q7: LLMClient 的模型切换是怎么实现的？

**答**：LLMClient 通过 langchain 的 `ChatOpenAI` 和 `ChatAnthropic` 封装多模型。DeepSeek 的 API 兼容 OpenAI 格式，所以只需修改 `base_url` 和 `api_key` 即可使用同一个 `ChatOpenAI` 实例。

不同 Agent 节点可以指定不同模型以优化成本：Plan 节点需要强推理能力用 deepseek-v4-flash，Reflect 节点可用更便宜的模型。`chat()` 方法的 `model` 参数覆盖全局默认值，满足差异化需求。

费用计算独立于 langchain 的 token counting，有自己的定价表 `_MODEL_PRICING`，确保即使 LLM API 不返回 usage 信息也能准确计费。

### Q8: ObservationPipeline 的 Noise Filter 是真的"删除"内容还是"隐藏"？

**答**：是真正的字符串删除（正则替换），不是 CSS display:none。删除前 HTML 可能 500KB（含大量 script/style/ad），删除后可能只有 10KB 的交互元素标记。被删除的内容不会进入 LLM 上下文，但原始 HTML 的存储路径通过 `raw_data_ref` 字段保留引用，如需事后审计可以回溯原始数据。

### Q9: ContextManager 的 Token 估算是怎么做的？为什么不直接用 tiktoken？

**答**：用字符数估算（英文 4 chars ≈ 1 token，中文 1 char ≈ 1 token），比 tiktoken 快（纯 Python 无 I/O），且不依赖模型特定的 tokenizer。误差在 10%-20% 以内，对于预算控制来说足够精确。Token 估算只需要知道"大概用了多少"，不需要精确到个位数，因为我们有 550 token 的安全余量。

### Q10: Phase 5 的 Agent 和 Phase 4 的 AuditGateway 是什么关系？

**答**：Phase 4 是"执行层"，Phase 5 是"编排层"。类比操作系统：Phase 4 的 AuditGateway+Skills 是 syscall 接口（提供安全的原子操作），Phase 5 的 AgentGraph 是 shell（负责命令解析、流程编排、状态管理）。

AgentGraph 不直接调用 Skill，而是通过 AuditGateway.invoke() 确保每一笔 Skill 调用都经过风险评估和审计日志。AgentGraph 负责"什么时候做什么"，AuditGateway 负责"这个操作能不能做"。
