# Phase 1: 后端骨架与基础设施 — 技术文档与面试题库

---

## 一、模块全景图

Phase 1 构建了项目的「神经系统」——它不处理业务逻辑，但让业务逻辑能够安全、可追溯、统一地运行。

```
请求进入 → RequestID中间件(注入request_id)
         → 路由处理
            → 正常? → APIResponse.success(data) → 200
            → 异常? → AppException → exception_handler → 结构化 {code, message, detail}
            → 未捕获异常? → 兜底 handler → 500
         → 响应头带 X-Request-ID 返回

所有环节：contextvars 保证 request_id 在 async 协程间透明传递
所有配置：settings 单例从 .env 加载
```

---

## 二、模块逐行解读

### 2.1 `config.py` — 全局配置中心

**设计动机**：消除硬编码。数据库连接字符串、Redis 地址、LLM API Key 等配置分散在各处时，切换环境（dev → staging → prod）需要改 N 个文件。`pydantic-settings` 的 `BaseSettings` 提供统一入口，所有模块通过 `from app.config import settings` 一行获取配置。

**核心源码解析**：

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",           # 自动加载 .env 文件
        case_sensitive=False,       # DB_HOST 和 db_host 等价
        extra="ignore",             # 未知环境变量不报错
    )

    DB_HOST: str = "127.0.0.1"     # 默认值，会被 .env/环境变量覆盖
    DB_PORT: int = 5432

    @property
    def database_url(self) -> str:  # 动态拼接，不是存死的字段
        return f"postgresql+asyncpg://..."
```

**关键点**：
- `@property` 让 `database_url` 每次访问时动态拼接。如果运行时修改 `settings.DB_HOST`，URL 自动反映变化。
- `asyncpg` vs `psycopg2`：`asyncpg` 是纯异步驱动，每个连接对应一个 TCP socket，在 asyncio 事件循环中以非阻塞方式工作，单连接可承载 10K+ QPS，而同步驱动需要线程池。

---

### 2.2 `exceptions.py` — 统一异常体系

**设计动机**：FastAPI 默认的异常返回格式不统一：`/docs` 路由 404 返回纯文本 `{"detail": "Not Found"}`，参数校验失败返回 Pydantic 格式的 `{"detail": [...]}`。前端需要一套一致的 `{code, message, detail}` 结构来编写全局错误处理逻辑。

**核心源码解析**：

```python
class AppException(Exception):
    def __init__(self, status_code, error_code, message, detail=None):
        self.status_code = status_code       # HTTP 状态码 (400/403/500)
        self.error_code = error_code_value    # 业务错误码 ("AGENT_STEP_LIMIT_EXCEEDED")
        self.message = message               # 人类可读消息
        self.detail = detail                 # 附加数据 (可为 dict/list)
```

**四个异常处理器的调用链**：
1. `AppException` → 我们主动抛出的业务异常，返回自定义 JSON
2. `StarletteHTTPException` → 框架层面的 HTTP 异常（404/405），统一格式化
3. `RequestValidationError` → Pydantic 校验失败时自动触发，提取字段错误
4. `Exception` → 兜底，防止裸奔

**`register_exception_handlers(app)` 模式**：FastAPI 的 `@app.exception_handler` 是装饰器语法糖，底层是向 `app.exception_handlers` 字典注册 `{ExceptionType: handler_func}`。封装为函数而不是在模块顶层直接写 `@app.exception_handler`，是因为模块加载时 FastAPI app 实例还不存在（app 在 `create_app()` 工厂函数中创建）。

---

### 2.3 `middleware/request_context.py` — 请求上下文

**设计动机**：在 async 服务中，`threading.local` 会串数据。Python 的 asyncio 在单线程内切换协程，多个请求共享同一条线程。一个请求写 `threading.local.request_id = "A"`，然后 await 让出控制权，另一个协程写 `threading.local.request_id = "B"`，当第一个协程恢复时读到的是 "B"。

`contextvars` 解决了这个问题：每个 asyncio Task 维护独立的 Context 字典。

**核心源码解析**：

```python
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

def set_request_id(rid: str) -> None:
    _request_id_ctx.set(rid)    # 写入当前 Task 的 Context

def get_request_id() -> str:
    return _request_id_ctx.get()  # 从当前 Task 的 Context 读取
```

**ContextVar 的底层实现**（CPython 源码级别）：
- 每个 `ContextVar` 在 C 层面是一个 `PyContextVar` 对象，携带唯一 ID
- 每个 `asyncio.Task` 维护一个 `dict[int, object]`（即 Context），key 是 ContextVar ID，value 是存储值
- `ContextVar.set()` → `PyContextVar_Set()` → 在当前 Task 的 Context dict 中写入 `{self.id: value}`
- `asyncio.create_task()` → `contextvars.copy_context()` → 浅拷贝父 Context → 传给子 Task

---

### 2.4 `middleware/request_id.py` — 请求 ID 中间件

**设计动机**：分布式系统中，一个用户请求可能经过多个微服务。如果每个服务生成自己的 ID，出问题时无法串联起完整链路。`X-Request-ID` 是业界标准：上游传入则透传，不传入则生成。

**核心源码解析**：

```python
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", "").strip() or uuid4().hex[:16]
        set_request_id(request_id)              # 注入 contextvars
        request.state.request_id = request_id   # 注入 request.state
        response = await call_next(request)     # 执行下游
        response.headers["X-Request-ID"] = request_id  # 写回响应头
        return response
```

**`BaseHTTPMiddleware` 的洋葱模型**：
```
        入站 → [dispatch 前半段] → call_next() → [dispatch 后半段] → 出站
                    ↓                                ↑
                  写入 context                    写响应头
                    ↓                                ↑
              ┌──────────────────────────────────────┐
              │      下一个中间件 / 路由处理          │
              └──────────────────────────────────────┘
```

---

### 2.5 `schemas/common.py` — 统一响应结构

**设计动机**：让前端解耦。所有接口返回 `{code: "SUCCESS", message: "...", data: ...}`，前端只需一种拦截器处理所有响应。

**核心源码解析**：

```python
T = TypeVar("T")  # 泛型参数，代表 data 的具体类型

class APIResponse(BaseModel, Generic[T]):
    code: str = "SUCCESS"
    message: str = "操作成功"
    data: T | None = None

    @classmethod
    def success(cls, data=None, message="操作成功") -> "APIResponse[T]":
        return cls(code="SUCCESS", message=message, data=data)
```

**`Generic[T]` 的工作方式**：当你写 `APIResponse[UserOut]` 时，Python 调用 `__class_getitem__` 返回一个参数化泛型别名。FastAPI 读取这个别名，在生成 OpenAPI schema 时，把 `data` 的类型替换为 `UserOut`，最终 Swagger 文档中能看到完整的数据结构。

---

## 三、面试题库

### Q1: Python 的 `@property` 底层原理是什么？（考察：描述符协议）

**考察意图**：区分"会用"和"理解底层"的候选人。

**答题思路**：从描述符协议入手，解释 `__get__` / `__set__` / `__delete__` 方法。

**参考答案**：
`property` 是一个**数据描述符**（同时定义了 `__get__` 和 `__set__`）。当你访问 `settings.database_url` 时：

1. Python 在 `type(settings).__dict__`（即 `Settings.__dict__`）中查找 `"database_url"`
2. 找到的是一个 `property` 对象（因为有 `@property` 装饰器）
3. 因为 `property` 是数据描述符，Python 调用 `property.__get__(settings, type(settings))`
4. `__get__` 内部执行你定义的 getter 函数，返回拼接后的 URL 字符串

关键区别：如果走实例 `__dict__` 查找（`settings.__dict__["database_url"]`），它不存在，因为 `property` 定义在类上而非实例上。

**延伸追问**：
- "`property` 和 `@cached_property` 的区别？" → `cached_property` 第一次访问后将结果写入实例 `__dict__`，后续访问绕过描述符直接从实例 dict 读取
- "描述符在 Django ORM 中哪里用到了？" → 模型字段（如 `CharField`）本质是描述符，`.save()` 时通过描述符协议管理字段值

---

### Q2: `pydantic-settings` 的 `BaseSettings` 是如何实现从 `.env` 加载的？（考察：类变量与实例变量的优先级链）

**考察意图**：理解配置加载的优先级机制。

**答题思路**：按优先级链解释 — 环境变量 > .env > 默认值。

**参考答案**：
`BaseSettings` 在实例化时的加载顺序：

1. 读取类属性定义中的**默认值**（`DB_HOST: str = "127.0.0.1"`）作为 baseline
2. 调用 `_env_file` 解析逻辑，读取 `.env` 文件中的键值对，**覆盖**同名的默认值
3. 调用 `os.environ` 读取系统环境变量，**再次覆盖**同名值

最终优先级：`环境变量 > .env > 默认值`。这个优先级设计来源于 12-Factor App 原则：环境变量是部署层面的配置，应该覆盖文件级配置。

底层用的是 `pydantic.fields.FieldInfo` 元数据 + `__init__` 中调用 `model_validate` 进行验证。`extra="ignore"` 表示遇到未知环境变量时不抛出 `ValidationError`。

**延伸追问**：
- "如果 `.env` 中有敏感信息，如何防止被意外提交到 Git？" → `.gitignore` + pre-commit hook + K8s Secret/Vault 注入
- "`model_config` 的 `case_sensitive=False` 在哪个阶段起作用？" → 在解析 `.env` 和环境变量时，做 `.lower()` 统一比较

---

### Q3: Python `contextvars` 和 `threading.local` 的区别？什么场景下 `threading.local` 会出 bug？（考察：asyncio 并发模型）

**考察意图**：区分多线程和协程并发的本质差异。

**答题思路**：从 asyncio 单线程多协程模型出发，解释为什么 thread-local 在 async 中失效。

**参考答案**：
- `threading.local`：数据绑定在 OS 线程的 `__dict__` 上。每个线程有自己的存储空间，线程内所有代码共享。
- `contextvars.ContextVar`：数据绑定在 `asyncio.Task` 的 Context dict 上。每个协程 Task 独立。

**为什么 `threading.local` 在 async 中会出 bug**：
asyncio 在单线程内通过事件循环调度多个协程。当协程 A 执行 `await` 时，控制权回到事件循环，事件循环调度协程 B 执行。A 和 B 在同一条线程上，如果 A 写了 `threading.local.x = "A"`，B 随后写了 `threading.local.x = "B"`，当 A 恢复执行时读到的是 "B"——这就是**协程间数据串扰**。

而 `contextvars` 在 `asyncio.create_task()` 时自动 `copy_context()`，子 Task 拿到的是父 Context 的浅拷贝，后续修改互不影响。

**延伸追问**：
- "`contextvars.copy_context()` 是深拷贝还是浅拷贝？" → 浅拷贝。Context 里的可变对象（如 list、dict）仍然是共享引用，修改它们会影响所有 Task。所以我们在 ContextVar 中只存不可变字符串（`str`, `int`）
- "如果要在后台 `asyncio.create_task()` 中获取当前请求的 request_id，需要怎么做？" → 在创建 Task 前用 `ctx = contextvars.copy_context()` 获取当前 Context，然后 `ctx.run(coro)` 运行。或者直接传参过去

---

### Q4: FastAPI 的 `BaseHTTPMiddleware` 和纯 ASGI Middleware 有什么区别？（考察：中间件双层机制）

**考察意图**：理解 Starlette/FastAPI 中间件的两种实现方式及取舍。

**答题思路**：对比两者的 API 形式、性能差异、适用场景。

**参考答案**：

| 维度 | BaseHTTPMiddleware | 纯 ASGI Middleware |
|------|-------------------|-------------------|
| API | 重写 `dispatch(request, call_next)` | 实现 `__call__(scope, receive, send)` |
| 请求体访问 | 可直接 `await request.body()` / `request.json()` | 需要手动处理 receive/send 流 |
| 性能 | 稍慢（内部用 `anyio` 流包装） | 更快（直接操作 ASGI 协议） |
| 适用场景 | 通用 HTTP 中间件（header 注入、日志） | 流式处理（SSE/WebSocket）、高性能场景 |

Starlette 的 `BaseHTTPMiddleware` 内部用 `anyio.create_memory_object_stream()` 做了请求/响应流的缓冲，这是为了让你能方便地访问 `request.body()`。代价是每个请求多一次内存拷贝。对于我们的 RequestID 中间件，只读 header 不读 body，理论上纯 ASGI 版本更快，但 `BaseHTTPMiddleware` 简洁性更好。

**延伸追问**：
- "为什么要设计洋葱模型？" → 让入站和出站逻辑对称配对（类似 Python 的 `with` 语句的 `__enter__` / `__exit__`）
- "如果中间件中 `await call_next()` 抛出异常，响应头还能写吗？" → 不能。因为异常抛出后 `call_next()` 之后的代码不执行。我们的代码中用 `try-except-raise` 模式，让异常穿透到 exception_handler，然后在 exception_handler 的响应中依然可以带 request_id

---

### Q5: `APIResponse[T]` 泛型是如何工作的？Pydantic 泛型和 Python typing 泛型的关系？（考察：泛型底层）

**考察意图**：理解 Python 类型系统的渐进式演进和 Pydantic 对泛型的扩展。

**答题思路**：先讲 Python 原生泛型（`Generic[T]` + `__class_getitem__`），再讲 Pydantic 如何扩展。

**参考答案**：
Python 3.9+ 支持 `Generic[T]` 语法。当你写 `class APIResponse(BaseModel, Generic[T])` 时：
1. `Generic[T]` 声明了一个类型参数 `T`
2. 运行时写 `APIResponse[UserOut]` 时，Python 调用 `APIResponse.__class_getitem__(UserOut)`
3. 返回一个 `_GenericAlias` 对象，内部保存了 `{T: UserOut}` 的映射

Pydantic 在此基础上做了两层扩展：
1. **模型字段的类型推断**：当 Pydantic 发现 `data: T | None = None` 时，在 `model_validate` 时不会对 `data` 做类型验证（因为 T 不定），但在 `model_dump` 序列化时不会报错
2. **OpenAPI Schema 生成**：FastAPI 在路由层推断响应模型时，针对 `APIResponse[list[UserOut]]`，展开 T=UserOut 后生成完整的 JSON Schema 给 Swagger 文档

**延伸追问**：
- "为什么不写 `T = TypeVar('T', bound=BaseModel)`？" → 加了 `bound=BaseModel` 后，`data` 只能是 Pydantic 模型，不能传 `str`、`int`、`None`。不加 bound 更灵活
- "`@classmethod` 和普通方法在泛型类中的行为有区别吗？" → classmethod 中的 `cls` 是参数化后的具体类（如 `APIResponse[UserOut]`），所以在 `success()` 中返回 `cls(...)` 能保留泛型信息

---

### Q6: Pydantic v2 的 `@computed_field` 和 `@property` 配合使用时，装饰器的叠加顺序为什么有影响？（考察：装饰器栈的底层执行顺序）

**考察意图**：理解 Python 装饰器的执行顺序和堆积机制。

**答题思路**：自下而上执行装饰器，最终暴露在最外层的是最上方的装饰器返回的值。

**参考答案**：

```python
@computed_field        # 第二步执行：computed_field(total_pages_property) → 标记为"应序列化"
@property              # 第一步执行：property(total_pages_method) → 生成 property 对象
def total_pages(self) -> int:
    ...
```

装饰器从最靠近函数定义的那个开始执行（自下而上）：
1. `@property` 先执行，把 `total_pages` 方法变成 property 描述符对象
2. `@computed_field` 后执行，接收 property 对象，将其标记为 Pydantic 的"计算字段"，用于序列化

如果反过来写（`@property` 在上面），Pydantic 的 `@computed_field` 先包装方法，然后 `@property` 再包一层，`computed_field` 的元数据可能会丢失，因为 property 不是 Pydantic 期望的 `FieldInfo` 类型。

**延伸追问**：
- "什么场景下 `@computed_field` 不适合使用？" → 当计算需要异步 I/O 时（如 `await db.fetch_count()`），因为 property 是同步的。应该把结果预先计算好，传入构造函数
- "Pydantic 如何区分 Field 和 computed_field？" → 内部元数据：Field 在 `model_fields` 字典中，computed_field 在 `model_computed_fields` 字典中

---

### Q7: 如果在高并发下，`uuid4().hex[:16]` 的碰撞概率够安全吗？（考察：数学直觉 + 工程权衡）

**考察意图**：考察候选人是否有「够用就行 vs 绝对安全」的工程判断力。

**答题思路**：计算碰撞概率，评估实际风险。

**参考答案**：
UUID4 hex 全长 32 个十六进制字符（128 bit）。截断到 16 字符 = 64 bit 熵。

碰撞概率用 Birthday Paradox 公式：
- 10 万条请求：\( P \approx \frac{n^2}{2 \times 2^{64}} \approx \frac{10^{10}}{3.7 \times 10^{19}} \approx 2.7 \times 10^{-10} \)
- 100 万条请求：\( P \approx 2.7 \times 10^{-8} \)
- 1 亿条请求：\( P \approx 0.00027 \)（0.027%）

单日 100 万请求级别的系统，碰撞概率约 10⁻⁸，远低于硬件故障导致的数据丢失概率。如果需要绝对唯一，用 `uuid7()`（时间戳前缀）+ 数据库 unique constraint 兜底。

**延伸追问**：
- "为什么不直接用完整 UUID4？" → 多 16 bytes，在微服务链路（HTTP Header → 网关 → 服务 A → 服务 B → 日志）中每个转发节点都会累积 header 体积。截断是 16 bytes vs 32 bytes 的权衡
- "`uuid4().hex` 和 `uuid4()` 的区别？" → `.hex` 返回不带连字符的 32 位十六进制字符串，`str(uuid4())` 返回带连字符的 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

---

### Q8: `ErrorCode(str, Enum)` 为什么同时继承 `str` 和 `Enum`？（考察：枚举继承与 JSON 序列化）

**考察意图**：理解 Python 枚举的多重继承和 JSON 序列化行为。

**答题思路**：如果只继承 `Enum`，序列化时会输出 `ErrorCode.NOT_FOUND` 而非 `"NOT_FOUND"`。

**参考答案**：
`ErrorCode(str, Enum)` 是 Python 3.11+ 推荐的自定义枚举写法。同时继承两个类意味着：
1. `str` 让枚举值是 str 的子类型，`isinstance(ErrorCode.NOT_FOUND, str)` 为 True
2. `Enum` 提供枚举的约束语义（只能取预定义值）
3. JSON 序列化时，`json.dumps({"code": ErrorCode.NOT_FOUND})` 输出 `{"code": "NOT_FOUND"}` 而非 `{"code": "ErrorCode.NOT_FOUND"}`

如果只写 `class ErrorCode(Enum)`，Pydantic 的 `model_dump()` 默认输出枚举成员名（`ErrorCode.NOT_FOUND`）而非其值。加上 `str` 继承后，Pydantic 检测到它是 str 子类，自动 `.value` 取值。

这个技巧利用了 Python 的 MRO（方法解析顺序）：`str` 在 `Enum` 之前，`__str__` 方法走 `str` 的实现，返回的就是值本身。

**延伸追问**：
- "为什么不用 `enum.StrEnum`？" → `StrEnum` 是 Python 3.11 才引入的，当前环境是 Python 3.10。`(str, Enum)` 是兼容写法
- "Mixin 类在前还是在后？" → 必须是 `(str, Enum)` 而非 `(Enum, str)`，因为 MRO 是 C3 线性化算法，前面的类优先级更高

---

## 四、Phase 1 测试覆盖总览

| 模块 | 测试数 | 覆盖场景 |
|------|-------|---------|
| config.py | 7 | 默认值、URL 拼接、环境变量覆盖、安全策略 |
| exceptions.py | 11 | 错误码枚举、AppException 构造、四个异常处理器 |
| middleware/ | 10 | ContextVar 读写、HTTP 中间件透传、异步隔离 |
| schemas/ | 11 | 响应序列化、泛型、分页计算、嵌套序列化 |
| **合计** | **39** | |

---

*文档生成日期：2026-05-25 | 作者：架构师 + Claude Code*
