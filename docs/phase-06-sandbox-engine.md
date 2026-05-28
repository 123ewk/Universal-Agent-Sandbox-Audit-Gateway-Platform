# Phase 6 — Playwright 沙箱引擎

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    Phase 5 AgentGraph                             │
│  Plan → Execute → Observe → Reflect                              │
│                     │                                             │
│         SkillContext(sandbox_engine=engine)                       │
└─────────────────────┬────────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────────┐
│              Phase 4 Browser Skills                               │
│  GotoSkill / ClickSkill / TypeSkill / ScreenshotSkill /           │
│  ExtractTextSkill                                                 │
│  engine = context.sandbox_engine                                  │
│  if engine → 真实执行, else → Mock 降级                            │
└─────────────────────┬────────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────────┐
│           Phase 6 SandboxEngine (per-session)                     │
│                                                                    │
│  navigate()    click()    type_text()    screenshot()             │
│  extract_text()    get_page_info()    capture_step_screenshot()   │
│                                                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │   Security   │  │  Screenshot  │  │       Provider          │ │
│  │  URL黑名单   │  │  截图保存/   │  │      适配器             │ │
│  │  route拦截   │  │  路径管理    │  │                         │ │
│  │  高危检测    │  │              │  │                         │ │
│  └──────────────┘  └──────────────┘  └───────────┬─────────────┘ │
│                                                   │               │
└───────────────────────────────────────────────────┼───────────────┘
                                                    │
          ┌─────────────────────────────────────────┼──────┐
          │              Provider                    │      │
          │                                          │      │
          │  LocalPlaywrightProvider (开发)           │      │
          │  ┌────────────────────────────────────┐  │      │
          │  │  Browser (1 process)               │  │      │
          │  │  ├─ BrowserContext (session_1)     │  │      │
          │  │  ├─ BrowserContext (session_2)     │  │      │
          │  │  └─ BrowserContext (session_3)     │  │      │
          │  └────────────────────────────────────┘  │      │
          │                                          │      │
          │  DockerPlaywrightProvider (未来)           │      │
          │  RemoteBrowserProvider (未来)              │      │
          └──────────────────────────────────────────┘      │
```

## Phase 6 文件结构

```
backend/app/sandbox/
├── __init__.py           # 模块标记
├── models.py             # PageInfo + ActionResult（dataclass 数据模型）
├── provider.py           # SandboxProvider 抽象接口
├── local_provider.py     # LocalPlaywrightProvider（本地 Chromium）
├── engine.py             # SandboxEngine — 浏览器操作统一封装
├── security.py           # 双层防御安全引擎
└── screenshot.py         # ScreenshotManager — 截图持久化

修改的文件：
├── skills/base.py        # SkillContext + sandbox_engine 字段
├── skills/browser.py     # 5 个 Browser Skill: if engine → 真实 else Mock
└── agent/graph.py        # AgentGraph: engine 生命周期 + 注入 + 观察
```

## 核心概念

### 1. 调用链路

```
Skill (GotoSkill.execute)
  → context.sandbox_engine.navigate(url)
    → security.check_url(url)          # 第 1 层：URL 安全审查
    → page.goto(url)                   # Playwright 实际导航
    → security.setup_route_interception(page)  # 第 2 层：网络拦截
    → screenshots.capture(page, ...)   # 自动截图
    → ActionResult {success, data, error, execution_time_ms}
  → SkillResult {success, data, error, execution_time_ms}
```

**Skill 不做浏览器操作**，只做参数校验 + 调用 engine + 结果映射。Playwright 完全封装在 SandboxEngine 内部。

### 2. SandboxProvider 抽象接口

**设计动机**：开发用本地 Chromium，生产用 Docker 容器隔离。通过依赖注入切换 Provider，业务代码零改动。

```python
class SandboxProvider(ABC):
    async def launch(self) -> None                          # 启动浏览器进程
    async def create_context(self, session_id) -> Context   # 创建 Session 的 BrowserContext
    async def destroy_context(self, session_id) -> None     # 销毁 Session 的 BrowserContext
    async def shutdown(self) -> None                        # 关闭整个浏览器进程
```

**隔离粒度选择 — BrowserContext**：

| 选项 | 隔离程度 | 创建速度 | 内存 | 选择 |
|------|---------|---------|------|------|
| Browser | 完全隔离 | 慢 (>2s) | 高 (~500MB) | |
| **BrowserContext** | 存储/cookie 隔离 | **快 (<100ms)** | **低 (~10MB)** | **选中** |
| Page | 无隔离 | 极快 | 极低 | 不够 |

每个 Agent Session 一个 BrowserContext，cookie/storage/cache 完全独立。

### 3. LocalPlaywrightProvider 实现

```python
class LocalPlaywrightProvider(SandboxProvider):
    """单 Browser 进程 + 多 BrowserContext（per session）"""

    async def launch(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )

    async def create_context(self, session_id):
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        self._contexts[session_id] = context
        return context
```

**Chromium 启动参数说明**：
- `--no-sandbox`：Docker 兼容
- `--disable-gpu`：无头模式关闭 GPU
- `--disable-dev-shm-usage`：避免 `/dev/shm` 不足导致崩溃
- `--disable-features=TranslateUI`：禁用翻译弹窗干扰

**生命周期管理**：`launch()` 幂等（已启动则跳过），`destroy_context()` 安全（session_id 不存在则静默返回），`shutdown()` 先关闭所有 Context 再关闭 Browser。

### 4. SandboxEngine API

每个 Agent Session 一个 SandboxEngine 实例，持有独立的 BrowserContext 和 Page。

```python
class SandboxEngine:
    # 核心操作
    async def navigate(url, timeout, wait_until) -> ActionResult
    async def click(selector, timeout) -> ActionResult
    async def type_text(selector, text, delay, clear) -> ActionResult
    async def screenshot(full_page) -> ActionResult
    async def extract_text(selector) -> ActionResult

    # 页面信息（供 ObservationPipeline 消费）
    async def get_page_info() -> PageInfo
    # 返回: {url, title, cleaned_text, interactive_elements, screenshot_path}

    # 步骤截图（Agent 每步执行后自动调用）
    async def capture_step_screenshot(step_number, action) -> str | None
```

**`get_page_info()` 的核心铁律**：不返回完整原始 HTML。只用 JS 提取 `cleaned_text`（前 500 字符预览）+ `interactive_elements`（button/input/a，最多 30 个）。原始 HTML 完全封装在 engine 内部。

**`extract_text()` 实现**：
```python
text = await page.evaluate(
    f"document.querySelector('{selector}')?.innerText || ''"
)
cleaned = re.sub(r'\n{3,}', '\n\n', text)  # 合并多余空行
```

**交互元素提取**（JS 注入）：
```javascript
document.querySelectorAll('button, input, a, select, textarea').forEach(el => {
    results.push({
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || el.placeholder || '').trim(),
        id: el.id, name: el.getAttribute('name'),
        type: el.type, href: el.href
    });
});
```

### 5. 双层安全防御

**第 1 层 — URL 黑名单**（`engine.navigate()` 调用前检查）：

| 拦截模式 | 示例 | 理由 |
|---------|------|------|
| `file://*` | `file:///etc/passwd` | 禁止访问本地文件系统 |
| `chrome://*` | `chrome://settings` | 禁止访问浏览器内部页面 |
| `javascript:*` | `javascript:alert(1)` | 防止 XSS 注入 |
| `about:blank` | — | 禁止空白页导航 |
| `data:*` | `data:text/html,...` | 禁止 data URI |

高危域名检测（不拦截但提高风险分）：bank, payment, transfer, admin, login, oauth。

**第 2 层 — Playwright Route 拦截**（网络请求级别）：

```python
# 拦截三类域名
BLOCKED_DOMAIN_CATEGORIES = {
    "ads":       ["doubleclick.net", "googleadservices.com", ...],
    "tracking":  ["google-analytics.com", "googletagmanager.com", "hotjar.com", ...],
    "malicious": ["malware", "phishing", "scam", ...],
}

async def intercept(route):
    if blocked_pattern in route.request.url:
        await route.abort()   # 直接阻止，不加载
    else:
        await route.continue_()
```

**高危行为检测**（点击/输入时检查 selector 和文本内容）：

| 行为关键词 | 风险 | 动作 |
|-----------|------|------|
| download / upload | 文件操作 | 需要 Human Approval |
| payment / submit | 金融/提交操作 | 需要 Human Approval |
| delete / signup | 账户操作 | 需要 Human Approval |

### 6. 截图系统

**存储结构**：
```
data/screenshots/
├── session_42/
│   ├── step_01_goto.png
│   ├── step_02_type.png
│   ├── step_03_click.png
│   └── step_04_final.png
```

**数据库只存路径引用**（绝不存 BLOB）：
```python
ScreenshotResult(
    path="data/screenshots/42/step_01_goto.png",
    filename="step_01_goto.png",
    session_id=42, step_number=1, action="goto",
    size_bytes=12345, width=1280, height=720,
    captured_at="2026-05-28T12:00:00Z",
)
```

**自动清理**：Session 结束时 `cleanup_session(session_id)` 删除整个截图目录。

### 7. Agent 集成链路

**完整流程**：

```
AgentGraph.invoke(task, session_id)
  │
  ├─ 1. SandboxEngine 创建
  │     engine = SandboxEngine(provider, session_id, security, screenshots)
  │     await engine.create_context()   ← BrowserContext + Page 创建
  │     self._engines[session_id] = engine
  │
  ├─ 2. _execute_node — 注入 engine
  │     context = SkillContext(sandbox_engine=engine)
  │     gateway.invoke(skill, params, context)
  │       → GotoSkill.execute(context, url=...)
  │           if context.sandbox_engine:     ← 有 engine
  │               result = engine.navigate(url)  ← 真实浏览器操作
  │           else:
  │               return Mock result         ← 无 engine 降级
  │
  ├─ 3. _observe_node — 消费真实页面数据
  │     engine = self._engines.get(session_id)
  │     if engine and engine.page:
  │         page_info = await engine.get_page_info()
  │         observation = ObservationRecord(
  │             summary=build_page_summary(page_info),
  │             page_title=page_info.title,
  │             page_url=page_info.url,
  │             interactive_elements=page_info.interactive_elements[:20],
  │         )
  │
  └─ 4. finally — SandboxEngine 清理
        await engine.cleanup()   ← 关闭 Page + 销毁 BrowserContext
        del self._engines[session_id]
```

### 8. 向后兼容设计

**核心模式**：`if engine → 真实执行, else → Mock 降级`

```python
class GotoSkill(BaseSkill):
    async def execute(self, context, **params):
        url = params.get("url", "")
        if not url:
            return SkillResult.fail("缺少必要参数: url")

        engine = context.sandbox_engine
        if engine is None:       # ← 测试/开发环境无 engine 时自动降级
            return SkillResult.ok(data={"url": url, "status": "navigation_scheduled"})

        result = await engine.navigate(url)  # ← 真实 Playwright 操作
        return SkillResult(success=result.success, data=result.data, ...)
```

- 现有 70 个 Phase 4 测试全部通过（`context.sandbox_engine` 默认为 None）
- 新增测试可以创建 Mock engine 来测试集成逻辑
- 生产环境注入真实 engine，开发/测试环境自动走 Mock

## 关键技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 隔离粒度 | BrowserContext（非 Browser/Page） | cookie/storage 隔离 + 创建快 <100ms + 内存低 ~10MB |
| 浏览器进程 | 单 Browser + 多 Context | 节省内存，Context 级别隔离已足够 |
| Engine 注入 | SkillContext.sandbox_engine 字段 | 显式类型，非 extra dict 隐式传递 |
| Skills 兼容 | `if engine → 真实 else Mock` | 零破坏，170+ 存量测试无需改动 |
| 安全位置 | engine 层 + route 层 | URL 检查在 nvigate() 中，资源拦截在 page.route() 中 |
| 原始 HTML | 不对外暴露 | engine 内部消化，只输出 cleaned_text + interactive_elements |
| 截图策略 | 每步自动截，路径存 DB | 完整操作证据链，不存 BLOB |
| Provider 切换 | 依赖注入 | 配置 `SANDBOX_PROVIDER`，不改业务代码 |
| 数据模型 | dataclass（非 Pydantic） | 高频创建场景，dataclass 零验证开销 |

## 测试覆盖（34 项单元测试）

**TestPageInfo（3 项）**：
- 空 PageInfo / 有数据 / to_dict 序列化

**TestActionResult（3 项）**：
- ok 工厂 / fail 工厂 / 默认构造

**TestSandboxSecurity（10 项）**：
- 正常 URL 放行
- file:// chrome:// about:blank javascript: 拦截
- 高危 URL（bank/admin）加分
- 自定义黑名单
- 高危行为检测（click submit / download / payment / 安全操作）
- route 拦截开关

**TestScreenshotManager（6 项）**：
- 文件名构建（单位数/双位数）
- 目录路径生成
- 目录创建 / 空 Session 查询 / 目录清理

**TestSkillBackwardCompat（7 项）**：
- goto/click/type/screenshot/extract_text 无 engine 时降级 Mock
- 参数校验仍然生效（缺少 url/selector 等）

**TestSandboxEngineIntegration（1 项）**：
- 未 create_context 直接 navigate 不崩溃

## 八股文 — 面试问答

### Q1: 为什么用 BrowserContext 隔离而不是独立 Browser 进程？

**答**：BrowserContext 是 Playwright 提供的轻量级隔离单元，提供了完整的 cookie/storage/cache 隔离，同时创建速度极快（<100ms）且内存开销低（~10MB）。独立 Browser 进程隔离更彻底但创建需要 2 秒以上且消耗 ~500MB 内存，对于 Agent Session（通常几分钟内完成）来说过于重型。

当前阶段 Context 隔离足够。生产环境需要更严格隔离时，可通过 DockerPlaywrightProvider 在容器中运行独立 Browser，接口不变。

### Q2: SandboxEngine 为什么不直接把 Playwright Page 暴露给 Skills？

**答**：三个原因：(1) **安全边界** — 如果 Skills 直接持有 Page，任何 Skill 都可以执行任意 JS、访问任意 URL、绕过安全审查；(2) **可替换性** — 如果 Skills 依赖 Playwright API，替换为 Puppeteer/Selenium 时需要改所有 Skill；(3) **测试性** — SandboxEngine 提供稳定 API 接口，测试时可以 Mock engine 而无需启动真实浏览器。

Skills 只看到 `engine.navigate(url)` → `ActionResult`，不知道底层是 Playwright 还是其他实现。

### Q3: 双层安全防御的设计逻辑是什么？

**答**：第 1 层（URL 检查）在 `engine.navigate()` 调用前运行，是事前防御 — 直接拒绝恶意 URL 的导航请求。第 2 层（route 拦截）在 Playwright 网络层运行，是事中防御 — 即使导航到了合法页面，该页面加载的第三方资源（广告/追踪/恶意脚本）也会被拦截。

两层互相独立，第 1 层失效不影响第 2 层（反之亦然）。高危行为检测是第 3 道防线 — 合法 URL 上的敏感操作（如下载、支付）仍然标记为需审批。

### Q4: `if engine → 真实 else Mock` 的向后兼容模式有什么好处？

**答**：这是一个刻意设计的降级策略。(1) 现有 70 个 Phase 4 测试不需要任何修改（它们不创建 engine，自动走 Mock）；(2) 开发阶段不需要启动真实浏览器就能跑 Agent 逻辑；(3) CI/CD 环境没有 Chromium 也能跑全部测试；(4) 生产环境注入 engine 后自动切换到真实浏览器模式。

这不是"临时方案" — 即使生产环境，某些非浏览器 Skill（如 file_read）也不需要 engine，Mock 降级始终是合法路径。

### Q5: `get_page_info()` 为什么不返回完整 HTML？

**答**：这是上下文铁律（Phase 5 设计）在 Sandbox 层的落地。完整 HTML 可能有 500KB+，包含大量 script/style/ad 等噪音，直接注入 LLM 上下文会快速耗尽 Token 预算且干扰 LLM 推理。

`get_page_info()` 通过 JS 注入只提取：(1) body.innerText 前 500 字符（cleaned_text）；(2) 可交互元素清单（button/input/a，最多 30 个）。这两个信息对 Agent 决策已足够，完整 HTML 在 engine 内部消化，不进入上层。

### Q6: Provider 抽象接口的设计原则？

**答**：(1) 最小接口 — 只有 4 个抽象方法（launch/create_context/destroy_context/shutdown），不预设具体实现；(2) 关注点分离 — Provider 只管理浏览器进程和 Context 生命周期，Security/Screenshot 逻辑在 engine 层；(3) 依赖注入 — AgentGraph 构造时接收 Provider 实例，不硬编码实现类；(4) 生命周期对称 — launch→shutdown、create→destroy，每条路径都有对应的清理。

后期演进：DockerPlaywrightProvider（容器隔离）、RemoteBrowserProvider（远程浏览器农场）、PooledProvider（BrowserContext 池化复用）。

### Q7: 截图为什么在 engine 层自动执行而不是在 Skills 中手动调用？

**答**：Skill 的职责是"执行一个原子操作"（导航/点击/输入），截图是"操作完成后的证据采集"，属于横切关注点。如果在每个 Skill 的 execute() 里手动调截图，会导致：(1) 代码重复（5 个 Skill 都要写截图逻辑）；(2) 容易遗漏（新增 Skill 时忘记截图）；(3) 截图时机不一致。

当前方案是 engine 层提供 `capture_step_screenshot()`，Agent 的 `_observe_node` 在每步执行后自动调用。截图逻辑集中在一个地方，所有 Skill 自动受益。

### Q8: 沙箱和 AuditGateway 的安全职责如何划分？

**答**：沙箱层（SandboxSecurity）负责"能不能执行" — URL 黑名单、网络拦截、高危行为标记。审计层（AuditGateway）负责"需不需要审批" — 风险评估、审计日志、审批流。

两者的配合：SandboxSecurity 返回 `SecurityCheck {requires_approval=True}` → AuditGateway 在 invoke() 时检查 → 需要审批则返回 `ApprovalRequired` ADT → Agent 暂停等待人类。两层互不依赖，各自独立决策，构成纵深防御。

## 后期演进

- **Docker Browser Pool**：预创建 Browser 进程池，消除 launch 延迟
- **Remote Browser**：浏览器运行在独立服务器上，Agent 通过网络连接
- **Vision Agent**：截图 + 多模态模型做视觉验证
- **Video Recording**：`browser.new_context(record_video_dir=...)` 录制完整操作视频
- **HAR Replay**：录制 HAR 文件，支持离线重放调试
- **Session Snapshot**：定期保存 BrowserContext 状态，支持断点续跑
- **Self-healing Selectors**：页面改版导致 selector 失效时，自动匹配最相似元素
