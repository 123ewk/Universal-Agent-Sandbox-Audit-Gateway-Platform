# Phase 4 — Skill Runtime + Risk Engine + Audit Gateway

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    LLM / Agent                              │
│  看到哪些 Skill 由 SkillSelector 控制（渐进式披露）           │
└──────────────┬──────────────────────────────────────────────┘
               │  invoke(skill_name, params)
               ▼
┌─────────────────────────────────────────────────────────────┐
│                    AuditGateway (单例)                      │
│                                                             │
│  1. registry.get(skill_name)       → 获取 Skill 实例        │
│  2. RiskEngine.assess(skill, params) → 风险评估             │
│  3. audit_log.create()             → 记录审计日志           │
│  4. 评估结果决策:                                           │
│     ├─ allow    → 直接执行                                  │
│     ├─ warn     → 执行但标记                                │
│     ├─ block    → 拒绝 + 返回错误                           │
│     └─ approval → 创建审批记录 → 返回 ApprovalRequired      │
│  5. skill.execute_with_timing()    → 执行 + 计时            │
│  6. audit_log.update()             → 更新执行结果           │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│              Skill 执行层                                     │
│  BrowserSkills  │  FileSkills  │  ShellSkills               │
│  (goto/click/   │  (read/      │  (run_command)             │
│   type/screenshot│   write)    │                            │
│   /extract_text) │              │                            │
└─────────────────────────────────────────────────────────────┘
```

## Phase 4 文件结构

```
backend/app/
├── skills/
│   ├── __init__.py        # init_skills() — 启动时自动注册所有 Skill
│   ├── enums.py           # SkillTier, SkillCategory, RiskLevel
│   ├── base.py            # BaseSkill(ABC), SkillResult, SkillContext
│   ├── registry.py        # SkillRegistry（单例）+ 按 tier/risk/category 查询
│   ├── selector.py        # SkillSelector — 渐进式披露选择器（NEW）
│   ├── browser.py         # 5 个 Browser Skills
│   ├── file.py            # 2 个 File Skills
│   └── shell.py           # 1 个 Shell Skill
├── engine/
│   ├── __init__.py
│   ├── risk.py            # RiskEngine — 双层风险评估
│   └── gateway.py         # AuditGateway — 审计网关
tests/
├── test_skill_runtime.py  # 70 个单元测试（6 个测试套件）
└── test_gateway_integration.py  # 9 个集成测试（需 PG）
```

## 核心概念

### 1. 渐进式技能披露 (Progressive Skill Disclosure)

**设计动机**：一次性给 LLM 注册 8 个 Tool 是浪费 Token 且不安全的。一个只做"导航+截图"的 Agent 不需要看到 `run_command`。

**4 级 Tier**：

| Tier | 技能 | 解锁方式 | 示例 |
|------|------|----------|------|
| `CORE` | goto, screenshot, extract_text | 始终可见 | 导航到 URL 查看页面内容 |
| `INTERACTION` | click, type | 自动解锁 | 点击按钮、输入搜索关键词 |
| `FILE` | read_file, write_file | 用户确认后 | 保存文件到沙箱 |
| `SHELL` | run_command | 人工审批 | 执行 Shell 命令 |

**核心流程**：
1. Agent 启动 → LLM 只看到 CORE 技能
2. Agent Planner 分析任务 → 调用 `detect_required_tiers()` 推断需要解锁的 Tier
3. 触发 `unlock(INTERACTION)` → LLM 现在看到 CORE + INTERACTION
4. 需要文件操作时 → 弹出确认 → 解锁 FILE
5. 需要执行命令时 → 人工审批 → 解锁 SHELL

**SkillSelector API**：
```python
selector = SkillSelector()
selector.unlock(SkillTier.INTERACTION)          # 解锁交互技能
tools = selector.get_llm_tools()                 # 返回 OpenAI function calling 格式
skill = selector.get_skill("browser_click")      # 获取 Skill 实例
selector.lock()                                   # 重置到仅 CORE
```

**自动推断**：
```python
# detect_required_tiers 通过关键词匹配推断所需 Tier
SkillSelector.detect_required_tiers("点击搜索按钮，输入关键词")
# → [SkillTier.INTERACTION]

SkillSelector.detect_required_tiers("读取 /tmp/data.txt，执行备份命令")
# → [SkillTier.FILE, SkillTier.SHELL]  # 按风险升序排列
```

**与 AuditGateway 的关系**：SkillSelector 负责"选"，AuditGateway 负责"执行"。Selector 决定 LLM 能看到什么，Gateway 决定能不能执行。两层互不依赖，保障安全。

### 1a. 按需 Skill 文档（skill.md）

**设计动机**：`get_llm_tools()` 返回的 OpenAI Function Calling 格式（名称+描述+schema）只够 LLM 知道"有哪些工具可用"。当 LLM 决定使用某个 Skill 后，需要更详细的用法说明——参数约束、安全规则、错误处理、示例。

**实现方式**：`get_skill_doc(name: str) → str | None`

```
get_llm_tools() → LLM 看到全部可用 Skill 的轻量 schema（始终加载）
     │
     ▼ LLM 选中 browser_click
     │
get_skill_doc("browser_click") → 加载 skill.md 完整内容（按需）
     │
     ▼ LLM 获得完整使用说明
```

**存储位置**：`backend/app/skills/descriptions/{skill_name}.md`

**8 个 skill.md 统一结构**：

```
Description      — 做什么 + 与其它 skill 的关系
When to use      — 什么场景调用
When NOT to use  — 什么场景不要用，改用哪个
Parameters       — 表格（含 default + 详细 description）
Returns          — JSON 结构说明
Risk Level       — L1-L5
Human Approval   — 是否需要审批
Security Rules   — 具体拦截规则表
Examples         — 2-3 个实际场景
Limits           — timeout + max_retry
Errors           — 表格（error / meaning / resolution）
```

**设计要点**：
- 每个 skill.md 约 1500-3000 字符，信息密度高于 schema 但远低于原始 HTML
- 按需加载 = 只有被选中的 Skill 才消耗 Token
- `load_skill_doc(name)` 直接读文件（无 Tier 检查）
- `selector.get_skill_doc(name)` 先检查 Tier 是否解锁

**参考开源**：
- **browser-use** 的 `Field(description=...)` 参数描述 + 系统提示词规则密集度
- **smolagents** 的 `Tool.description` + `inputs` 文档风格
- 两层信息模型（schema 轻量前置 + skill.md 按需加载）是我们的独特设计

### 2. 风险等级体系 (L1-L5)

对应 ShadowOS 五级风险模型：

| 等级 | 分值 | 操作类型 | Gateway 动作 |
|------|------|----------|--------------|
| L1 | 1-20 | 只读（导航、截图） | 直接执行 |
| L2 | 21-40 | 交互（点击、输入） | 记录审计后执行 |
| L3 | 41-60 | 文件操作 | 执行但标记警告 |
| L4 | 61-80 | Shell 执行 | 需要人工审批 |
| L5 | 81-100 | 高危破坏 | 直接拦截 |

**双层评估 (Two-Pass Assessment)**：
1. **静态声明**：每个 Skill 在类定义时确定 `risk_level`
2. **动态分析**：RiskEngine 检查实际参数中的风险关键词（如 bank URL → 加分，`rm -rf` → 拦截）

### 3. 审计网关 (AuditGateway)

**核心职责**：所有 Skill 调用的唯一入口，强制执行"事前审计→事中执行→事后记录"。

**`invoke()` 执行流程**：
```
1. registry.get(skill_name)        → 获取 Skill 实例
2. RiskEngine.assess(skill, params) → 风险评估
3. AuditLog.create()               → 记录审计日志（status=PENDING）
4. 评估结果决策:
   ├─ blocked   → 更新日志为 BLOCKED → 返回 SkillResult.fail()
   ├─ allow     → 直接执行 → 更新日志为 SUCCESS
   └─ approval  → 创建 ApprovalRecord → 返回 ApprovalRequired ADT
5. skill.execute_with_timing()      → 实际执行
6. audit_log.update()               → 更新 status/result
```

**ApprovalRequired 代数数据类型 (ADT)**：
```python
@dataclass
class ApprovalRequired:
    approval_record_id: int    # 审批记录 ID
    assessment: RiskAssessment  # 风险评估详情
    audit_log_id: int          # 审计日志 ID
```

调用方通过 `isinstance(result, ApprovalRequired)` 判断是否需要暂停等待审批。

**审批恢复**：
```python
result = await gateway.execute_approved(
    approval_record_id=42,
    context=ctx,
    db=db_session,
)
```
`execute_approved()` 验证审批记录状态为 APPROVED，然后以 `bypass_approval=True` 重新调用 `invoke()` 绕过审批检查直接执行。

### 4. Skill 执行上下文与结果

```python
@dataclass
class SkillContext:
    session_id: int
    request_id: str
    sandbox_id: str | None

@dataclass
class SkillResult:
    success: bool
    data: Any
    error: str | None
    execution_time_ms: int  # 由 execute_with_timing() 自动计时

    @classmethod
    def ok(cls, data=None)    # 快捷构造成功
    @classmethod
    def fail(cls, error, data=None)  # 快捷构造失败
```

### 5. SkillRegistry 自动发现

```python
registry.discover()  # 扫描所有 BaseSkill.__subclasses__()，自动注册
```

启动时调用 `init_skills()` 导入所有 Skill 模块触发 __subclasses__() 注册。

### 6. 安全沙箱策略（文件/Shell）

**File Skills** — 敏感路径黑名单：
```python
SENSITIVE_FILE_KEYWORDS = [
    "/etc/", "/root/", "/.ssh/", "/.git/", "/var/log/",
]
```

**Shell Skills** — 危险命令黑名单：
```python
BLOCKED_COMMAND_KEYWORDS = [
    "rm -rf /", "dd if=", "mkfs", "format",
    "chmod 777", ":({",  # Fork bomb
    "nmap", "hydra", "sqlmap",
    "wget ", "curl ", "nc ",  # 数据外泄工具
]
```

## 关键技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| `__init_subclass__` 验证 | 类定义时（非实例化时） | 尽早捕获错误，元编程自动执行 |
| Tier 控制粒度 | Enum 级别（非单个 Skill） | 简化 Agent 逻辑，符合语义分组 |
| Registry 单例 | 模块级变量 | 全应用共享同一注册表，避免不同请求看到不同 Skill 集合 |
| RiskLevel 类型 | IntEnum | 支持比较运算（L1 < L5），存数据库为 int |
| SkillSelector 状态 | 每 Agent Session 独立 | 不共享状态，线程安全 |
| execute_with_timing | 封装而非继承 | 所有 Skill 自动获得计时能力，不侵入业务逻辑 |
| ApprovalRequired | dataclass ADT | 显式类型区分，调用方用 isinstance() 判断 |
| decode_responses | Redis 连接池级别 | 避免每个查询手动 decode bytes |

## 测试覆盖（70 单元测试 + 9 集成测试）

**提交历史**：
- `c973dcb` ~ `6342ba1` — 核心 Skill Runtime + Risk Engine + Audit Gateway + 渐进式披露
- `edf659f` — 升级 8 个 skill.md 到开源级别颗粒度（含 when to use/not use、多示例、错误表）

**测试套件 1 — BaseSkill（3 个）**：
- 子类不定义 name → TypeError
- 子类不定义 description → TypeError
- 正确定义的子类可创建

**测试套件 2 — SkillResult / SkillContext（6 个）**：
- 默认构造、ok/fail 快捷构造、execution_time
- SkillContext 默认值、自定义值

**测试套件 3 — SkillRegistry（9 个）**：
- 注册/获取/重复注册/不存在的 Skill
- 按 risk/category/tier 筛选
- count/discover 自动发现
- **新增**：list_by_tier / list_by_tiers

**测试套件 4 — Browser Skills（9 个）**：
- goto 缺少 url → 失败
- click 缺少 selector → 失败
- type 缺少 selector → 失败
- type/text/screenshot/extract_text 正常参数 → 成功

**测试套件 5 — File Skills（6 个）**：
- 缺少 path → 失败
- 正常文件读取 → 成功
- 敏感文件（/etc/passwd, /etc/shadow）→ 拦截
- 写入敏感路径 → 拦截

**测试套件 6 — Shell Skills（6 个）**：
- 缺少 command → 失败
- 安全命令 → 成功
- rm -rf /、Fork bomb、curl → 拦截

**测试套件 7 — RiskEngine（9 个）**：
- L1-L4 基础分计算
- 银行 URL 加分
- file:// chrome:// 拦截
- 空参数不崩溃
- to_dict JSON 序列化

**测试套件 8 — SkillSelector（19 个）**：
- 初始状态、自定义初始 Tier
- unlock/lock/is_unlocked
- 可见技能过滤（CORE/INTERACTION）
- get_skill Tier 检查
- get_llm_tools OpenAI format
- detect_required_tiers 关键词检测
- 风险升序排列
- estimate_tier_description

**集成测试 — AuditGateway（9 个，需 PG）**：
- L1 直接执行
- L2 审计记录
- L4 需要审批
- 审批→执行流程
- blocked URL 拦截
- 不存在的 Skill
- 审批前拒绝
- 计时记录
- bypass_approval 绕过

## 八股文 — 面试问答

### Q1: 为什么选择 `__init_subclass__` 而非 ABC `@abstractmethod` 验证 Skill name/description？
**答**：`__init_subclass__` 在类定义时（而非实例化时）触发，能更早捕获错误。ABC 的 `@abstractmethod` 只在实例化时报错，而 `__init_subclass__` 在类创建时通过 `raise TypeError` 直接阻止模块加载。这使得开发者写 class 定义时就能得到反馈，而非等到运行时。

### Q2: SkillSelector 为什么要用 `isinstance(result, ApprovalRequired)` 而非异常处理？
**答**：ApprovalRequired 是一个代数数据类型（ADT），表示"执行流程中的一种可能状态"。使用 ADT 而非异常的核心区别在于：异常用于异常情况（崩溃、超时），但"需要审批"是正常业务流程的一个分支。使用 isinstance() 判断是显式的、类型安全的模式匹配，调用方不会意外忽略这个分支。用异常的话，调用方可能忘记 try/except。

### Q3: RiskEngine 的双层评估是怎么实现的？
**答**：第一层是静态声明 — 每个 Skill 在类定义时声明 `risk_level`（如 L1-L5），这是固定属性。第二层是动态参数分析 — RiskEngine 的 `assess()` 方法在运行时检查传入参数，通过 `_flatten_params()` 递归展开所有参数值，与 `_risk_keywords` 字典匹配（如 "bank" 加 30 分）。最终分数 = max(基础分, 关键词总分)，再映射回 L1-L5。这种双层设计保证了安全策略的冗余检查。

### Q4: Tier 和 RiskLevel 有什么区别？
**答**：Tier 控制"可见性"（LLM 能看到哪些 Skill），RiskLevel 控制"执行权限"（能不能执行）。前者是使用场景分组（CORE/INTERACTION/FILE/SHELL），后者是安全等级（L1-L5）。举例：goto 是 CORE 始终可见，但如果有风险参数（如 `file:///etc/passwd`），RiskEngine 仍会拦截。两者互不依赖，构成正交的安全控制。

### Q5: 为什么 detect_required_tiers 用关键词匹配而非 LLM 推断？
**答**：关键词匹配在 Agent Planner 阶段使用，目的是在调用 LLM 之前快速确定需要解锁哪些 Tier。这是一个"预过滤"步骤：
- 优点：零延迟、零 Token 消耗、确定性强、可测试
- 不足：无法处理复杂语义（如 "帮我查一下服务器时间" 可能隐含 shell 执行）
- 补充方案：Agent Planner 的 LLM 可以在生成计划时显式标注需要解锁的 Tier，关键词匹配作为保底机制

### Q6: SkillRegistry.discover() 为什么不递归扫描子类？
**答**：我们的 Skill 体系只有一层继承（BaseSkill → 具体 Skill），没有中间抽象类。`__subclasses__()` 只返回直接子类，恰好符合这个设计。如果有多层继承需求，可以用 `__init_subclass__` 钩子在每个子类创建时主动注册，或者递归调用 `__subclasses__()` 并展平。

### Q7: `execute_with_timing()` 为什么用封装而非装饰器？
**答**：因为 Skill 执行是通过 AuditGateway 统一调用的，调用方知道何时需要计时（每次调用都需要）。封装在 BaseSkill 中的 `execute_with_timing()` 方法：
- 让子类不必关心计时逻辑
- 统一的异常→SkillResult 转换
- 比装饰器更显式、更容易单测

装饰器更适合"横切关注点"（如所有函数都需要日志），而计时在这里是 Skill 执行流程的一个固定环节。

### Q8: Phase 4 的 8 个 Skill 中，哪些真正"执行"了什么？
**答**：Phase 4 的 Skill 是接口定义 + 基础校验 + Mock 实现。真正的浏览器自动化（Playwright）和沙箱执行（Docker）将在 Phase 5 接入。当前每个 Skill 的 execute() 只做：
1. 参数校验（如缺少 url/selector/path/command → 返回失败）
2. 安全检查（敏感文件拦截、危险命令拦截）
3. 返回 Mock 结果（如 `{"status": "navigation_scheduled"}`）

这遵循了 ShadowOS 的"先搭骨架，再填血肉"策略。

### Q9: SkillSelector 的线程安全模型是怎样的？
**答**：SkillSelector 不是线程安全的，也不打算设计为线程安全。每个 Agent Session 拥有自己的 Selector 实例，不存在共享状态。这种"每 Session 独享实例"的模型：
- 消除了锁竞争
- 状态隔离（Session A 解锁了 SHELL 不影响 Session B）
- 与 LangGraph 的 Session 模型天然对齐

SkillRegistry（全局单例）是只读的（注册在启动时完成），所以不需要线程安全。

### Q10: AuditGateway 的 `bypass_approval` 参数会不会有安全风险？
**答**：`bypass_approval=True` 只在 `execute_approved()` 内部使用，而 `execute_approved()` 是审批流中的第二步 —— 第一步 `invoke()` 已经创建了审批记录并等待人工批准。`bypass_approval` 的作用是在审批通过后避免重复等待，而不是跳过审批。如果直接暴露给外部调用，确实有风险，所以它的使用限定在 Gateway 内部控制流中，外部 API 层不暴露这个参数。
