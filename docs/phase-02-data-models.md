# Phase 2: 数据模型层 — 技术文档与面试题库

---

## 一、模块全景图

Phase 2 构建了项目的持久化层，是 Agent 执行数据的「物理仓库」——它不执行业务逻辑，但让所有 Agent 操作有迹可循、有据可查。

```
FastAPI 路由
    │
    ▼
Depends(get_db_session())    ← 依赖注入：每个请求一个独立 session
    │
    ▼
SQLAlchemy AsyncSession      ← async 连接 pg，非阻塞 I/O
    │
    ├─ db.add(session)        → INSERT INTO agent_sessions ...
    ├─ await db.commit()      → 成功→持久化
    │                          → 失败→回滚
    └─ await db.refresh(obj)  → 重新查询获取数据库中的最新值
            │
            ▼
        asyncpg               ← 纯异步 PostgreSQL 驱动
            │
            ▼
        PostgreSQL agent_sandbox
            ├─ agent_sessions      ← 任务会话表
            ├─ audit_logs          ← 审计日志表
            └─ approval_records    ← 审批记录表
```

### Alembic 迁移管线

```
ORM 模型变更 → alembic revision --autogenerate → 生成迁移脚本
                                                        │
                                                        ▼
                                              alembic upgrade head → 应用到数据库
```

---

## 二、模块逐行解读

### 2.1 `database.py` — 异步引擎与会话管理

#### 设计动机

FastAPI 是 async 框架，路由函数都是 `async def`。如果数据库操作使用同步驱动（比如 `psycopg2`），需要丢到线程池（`run_in_executor`），不仅增加上下文切换开销，还会在数据库慢查询时阻塞线程。SQLAlchemy 的 `async` 支持 + `asyncpg` 驱动让数据库 I/O 在事件循环中以非阻塞方式运行，单连接可承载数千并发查询。

```python
_engine = create_async_engine(
    settings.database_url,
    echo=settings.DB_ECHO,
    pool_size=10,              # 连接池保持 10 个连接
    max_overflow=20,            # 突发流量时最多额外创建 20 个
    pool_pre_ping=True,         # 每次取连接前先 ping 一下
    pool_recycle=3600,          # 连接 1 小时后回收
)

_SessionFactory = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,    # 防止 commit 后懒加载失败
)
```

**为什么 `expire_on_commit=False` 是必要的？**

默认情况下，SQLAlchemy 在 `commit()` 后会把 session 中所有对象标记为"已过期"（expired）。下次访问对象的任何属性时，SQLAlchemy 会发起懒加载查询（lazy load）。在 async 模式中，懒加载查询需要活动事务，而 commit 后事务已经关闭 → 引发 `DetachedInstanceError`。设置 `expire_on_commit=False` 后，commit 不会过期对象，数据保留在内存中供读取。

```python
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个请求一个独立 session，自动 commit/rollback/close"""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()       # 正常 → 提交
        except Exception:
            await session.rollback()     # 异常 → 回滚
            raise                        # 不吞异常
        finally:
            await session.close()        # 归还连接
```

**为什么用 `yield` 而非 `return`？**

FastAPI 的 `Depends` 支持生成器语法：`yield` 之前的代码是依赖的构造部分，`yield` 之后的代码在请求结束后自动执行。这天然适配了「打开 session → 使用 → 关闭 session」的生命周期模式，避免在每个路由中手动写 `try-finally`。

---

### 2.2 `models/base.py` — ORM 基类与 Mixin

#### 设计动机

每个数据表都有 `id`、`created_at`、`updated_at` 三个公共字段。如果在每个模型中重复定义，不仅啰嗦，还容易引入不一致（比如有的表用 `create_time`、有的用 `created_at`）。

```python
class BaseModelMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

**为什么 Mixin 不继承 `DeclarativeBase`？**

SQLAlchemy 2.0 要求每个 ORM 模型直接或间接继承 `DeclarativeBase`（因为 `Base` 负责 metadata 收集）。如果 `BaseModelMixin` 也继承 `DeclarativeBase`，然后业务模型又继承 `BaseModelMixin` 和 `Base`，会触发 C3 线性化冲突（菱形继承）。Mixin 只定义列，不声明 `__tablename__`，由最终模型决定映射到哪张表。

**`server_default=func.now()` vs `default=func.now()`？**

前者由数据库在 INSERT 时生成时间戳（`DEFAULT now()`），后者由 Python ORM 层面生成。使用 `server_default` 的好处是：即使绕过 ORM 直接执行 SQL INSERT（比如迁移脚本、后台批量导入），数据库也会自动填充时间戳。

---

### 2.3 三个业务模型

#### AgentSession — 任务会话

```python
class SessionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    APPROVAL_PENDING = "approval_pending"  # ⭐ Human-in-the-loop 核心状态
```

`APPROVAL_PENDING` 是项目独创状态：Agent 触发高危操作并被暂停等待人类审批。前端 Vue 3 轮询或 WebSocket 推送此状态时，弹出红灯警告弹窗。

#### AuditLog — 审计日志

```python
class AuditLog(BaseModelMixin, Base):
    session_id: Mapped[int]      # FK → agent_sessions.id
    action_type: Mapped[str]     # navigate / click / type / screenshot / ...
    action_input: Mapped[dict]   # {"url": "https://bank.com/transfer"}
    is_high_risk: Mapped[bool]   # 是否高危
    approved: Mapped[bool]       # None=未审 True=通过 False=拒绝
    execution_time_ms: Mapped[int]  # 操作耗时（毫秒）
    action_taken_at: Mapped[datetime]  # 原始执行时间
```

**`approved` 字段的三态设计**：`None` = 未审批（不是高危操作），`True` = 已通过，`False` = 已拒绝。这使得同一张表既能记录普通操作，也能记录审批操作，通过 `is_high_risk` 区分。

#### ApprovalRecord — 审批记录

```python
class ApprovalRecord(BaseModelMixin, Base):
    session_id: Mapped[int]      # FK
    audit_log_id: Mapped[int]    # FK → 触发审批的审计日志
    risk_type: Mapped[str]       # financial_action / file_operation / ...
    risk_description: Mapped[str] # "Agent 尝试点击银行转账按钮"
    risk_score: Mapped[int]      # 0-100 危险等级评分
    action_context: Mapped[dict] # 上下文快照（URL、截图路径等）
    expires_at: Mapped[datetime] # 审批超时时间
```

---

### 2.4 Alembic 迁移配置

```python
# env.py
from app.config import settings
from app.models.base import Base

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

**为什么 Alembic 需要同步和异步两种 URL？**

`alembic init -t async` 生成的是异步模板，使用 `async_engine_from_config()` 创建异步引擎。但在离线模式（`run_migrations_offline`）中，URL 只是告诉 Alembic 用哪个方言生成 `.sql` 文件（`PostgresqlImpl` vs `MysqlImpl` 等），不实际连接数据库。在在线模式中，Alembic 使用 async engine 连接数据库执行 DDL。

---

## 三、面试题库

### Q1: SQLAlchemy 2.0 的 `Mapped[]` 注解相比旧版的 `Column()` 有什么优势？（考察：Python 类型系统进化）

**考察意图**：了解 SQLAlchemy 2.0 的声明式 API 设计哲学。

**答题思路**：从类型检查、PEP 484/526 支持、future-style 三个方面展开。

**参考答案**：

| 维度 | 旧版 `Column()` | 新版 `Mapped[]` |
|------|----------------|-----------------|
| mypy 推断 | 推断为 `InstrumentedAttribute` | 推断为对应 Python 类型（`str`、`int`） |
| IDE 补全 | 无法提示对象属性 | 完整补全，因为类型已知 |
| 列约束定义 | `Column(String, index=True)` | `mapped_column(String, index=True)` |
| 类型 vs 约束 | 混在 `Column()` 中 | `Mapped[type]` 管类型，`mapped_column()` 管约束 |

```python
# 旧版 1.x — 类型不明确
name = Column(String(64), nullable=False)

# 新版 2.0 — 类型清晰分离
name: Mapped[str] = mapped_column(String(64), nullable=False)
```

SQLAlchemy 2.0 将"Python 类型"和"数据库列约束"分离到 `Mapped[]` 和 `mapped_column()` 两个语法结构中。`Mapped[str]` 告诉 mypy 和 IDE 这个属性是 `str` 类型，`mapped_column(String(64))` 告诉 SQLAlchemy 在数据库中创建 `VARCHAR(64)` 类型的列。

**延伸追问**：
- "`Mapped[Optional[str]]` 和 `Mapped[str | None]` 的区别？" → 语义相同，`str | None` 是 Python 3.10+ 语法。底层都映射为 `nullable=True` 的列
- "`mapped_column` 和 `Column` 可以混用吗？" → 不建议。在新 API 中所有列都应该用 `Mapped[]`+`mapped_column()`，混用可能导致类型推断错误

---

### Q2: `pool_pre_ping=True` 和 `pool_recycle=3600` 解决了什么问题？（考察：数据库连接池运维）

**考察意图**：考察候选人是否有处理生产环境数据库连接问题的经验。

**答题思路**：从连接断开场景（网络闪断、防火墙超时、数据库重启）切入。

**参考答案**：

`pool_pre_ping=True`：每次 `engine.connect()` 从连接池中取连接时，SQLAlchemy 会先执行 `SELECT 1` 检查连接是否存活。如果连接已断开（比如网络闪断），引擎会自动丢弃该连接并创建新连接。不开启的话，拿到死连接的请求会报 `OperationalError: connection already closed`。

`pool_recycle=3600`：大多数数据库和中间代理（PgBouncer、ProxySQL、AWS RDS Proxy）都会在 1~2 小时后关闭空闲连接。如果连接池持有旧的 TCP 连接，在 1 小时后使用会报 `broken pipe`。`pool_recycle=3600` 强制在连接存活满 1 小时时回收，在数据库自动断连之前主动替换。

**延伸追问**：
- "`max_overflow` 和 `pool_size` 的合理比例？" → 经验值是 `pool_size=10, max_overflow=20`，即基线 10 连接，突发最多 30。`max_overflow` 过大可能压垮数据库连接数
- "用 `NullPool` 的代价？" → 每个请求都建立新 TCP 连接（三次握手），RT 增加约 10-50ms。适合测试和短连接场景，不适合高吞吐生产环境

---

### Q3: `async_sessionmaker` 和 `async def get_db_session()` 中的 `yield` 关键字是如何实现"请求结束自动提交"的？（考察：FastAPI 依赖注入的协程上下文管理器机制）

**考察意图**：理解 FastAPI Depends 对 async generator 的特殊处理。

**答题思路**：从 context manager 协议 + ASGI lifecycle 事件两个层面解释。

**参考答案**：

FastAPI 的 `Depends()` 底层检测依赖函数是否是 **async generator**（函数体中包含 `yield`）：

1. 依赖函数被调用到 `yield` 之前：FastAPI 将其作为"依赖构造"，在路由处理前执行
2. `yield` 的值作为参数注入到路由函数中
3. 路由函数执行完毕后（或抛出异常后），FastAPI 调用 generator 的 `__anext__()`，执行 `yield` 之后的代码

从源码层面看，Starlette 的 `Dependant` 类会在 `solve_dependencies()` 中检测到 `yield`，然后把 generator 包装为上下文管理器：

```python
# 伪代码：FastAPI 底层逻辑
ctx = contextmanager(yield_func)
async with ctx as session:
    response = await route(request, session=session)  # yield 的值注入
# yield 之后的代码在这里执行 → commit() 或 rollback()
```

这意味着 **`yield` 之后的 `commit()` 和 `close()` 是在响应已发送到中间件栈后执行的**。如果 commit 失败（比如唯一约束冲突），FastAPI 会将异常冒泡到全局 `exception_handler`，此时服务器已经向客户端返回了 200 状态码——这在实际业务中是一个关键风险点。

**延伸追问**：
- "如果在 `yield` 之后的 `commit()` 中抛出了异常，但响应已经返回了 200，前端该怎么办？" → 双阶段提交陷阱。解决方案：在路由中先 `commit()` 再返回响应，`get_db_session()` 不再 `yield` 后自动 commit，或者用 outbox pattern
- "普通 `return` 和 `yield` 在 Depends 中的区别？" → `return` 的依赖在请求结束后没有清理阶段。`yield` 有"前处理"和"后处理"两个阶段

---

### Q4: `ServerDefault=func.now()` 和 Python 层的 `default=datetime.now()` 有什么区别？（考察：ORM 层 vs 数据库层的设计边界）

**考察意图**：区分业务层和持久层的时间戳职责。

**答题思路**：从数据库时区一致性、绕过 ORM 的场景、时间精度三个角度对比。

**参考答案**：

| 方式 | 生成时机 | 谁生成 | 绕过 ORM 时是否生效 |
|------|---------|-------|------------------|
| `server_default=func.now()` | INSERT 执行时 | 数据库 | ✅ 生效 |
| `default=func.now()` | SQL 语句构建时 | Python | ❌ 失效 |
| `default=datetime.now()` | 模型实例化时 | Python | ❌ 失效 |

`server_default` 在数据库中翻译为 `DEFAULT now()`（PostgreSQL）或 `DEFAULT CURRENT_TIMESTAMP`（MySQL）。当你直接执行 `INSERT INTO agent_sessions (task_description) VALUES ('test')` 时，数据库自动填充 `created_at`。而 `default=datetime.now()` 只在 `session = AgentSession(task_description='test')` 的瞬间获取时间，如果直接执行 SQL 批量插入，`created_at` 会是 NULL（如果字段可空）或报错（字段不可空时）。

另一个关键区别：`server_default` 使用**数据库服务器的当前时间**。如果应用服务器和数据库服务器不在同一时区（或者时间不同步），`server_default` 得到的是数据库侧时间，`default=datetime.now()` 得到的是应用侧时间。使用 `DateTime(timezone=True)` + `server_default` 确保所有时间戳都在数据库侧统一生成。

**延伸追问**：
- "`onupdate` 在 UPDATE 时是如何触发的？" → SQLAlchemy 在 UPDATE 语句中自动添加 `SET updated_at = now()`。但如果你执行 `session.execute(update(...))` 而不通过 ORM 的 `commit()`，onupdate 不会触发
- "什么场景下应该用 Python 层的 `default`？" → 当时间戳需要由业务逻辑决定（如"记录操作的实际发生时间"）且数据库和应用是同一台机器时

---

### Q5: PostgreSQL ENUM 和 VARCHAR + CHECK 约束的优劣对比？（考察：数据库 schema 设计取舍）

**考察意图**：了解不同方案的工程取舍。

**答题思路**：从性能、迁移难度、兼容性三个维度对比。

**参考答案**：

```python
# 方案 A：原生 ENUM（create_constraint=True）
session_status = mapped_column(
    SAEnum(SessionStatus, name="session_status_enum", create_constraint=True)
)

# 方案 B：VARCHAR + CHECK
session_status = mapped_column(
    String(20), default=SessionStatus.PENDING
)
```

| 维度 | 原生 ENUM | VARCHAR + CHECK |
|------|----------|----------------|
| 存储空间 | 1-2 bytes（内部整数） | 20 bytes 字符串 |
| 插入性能 | 极快（整数比较） | 稍慢（字符串比较） |
| 增加新值 | `ALTER TYPE ... ADD VALUE`（事务外执行） | `ALTER TABLE ... DROP CONSTRAINT; ... ADD CONSTRAINT` |
| 兼容性 | PostgreSQL 专属 | 所有数据库通用 |
| Schema 变更 | 麻烦（需要新建 TYPE 再迁移） | 简单（ALTER TABLE） |

**决策依据**：对于 `SessionStatus` 这种变化极低频的枚举（开发阶段可能需要加一个新状态），原生 ENUM 的性能收益不大，但迁移成本高。VARCHAR + 应用层校验（Pydantic 的 `Literal` 约束）在实际工程中往往更灵活。

但在这个项目中选择了原生 ENUM，原因是：
1. `SAEnum()` 自动映射 Python enum → PostgreSQL enum，DDL 由 Alembic 自动生成
2. 状态不允许数据库中出现非法值，ENUM 提供数据库层面的硬约束
3. 加新状态的场景极少（开发阶段才会调整）

**延伸追问**：
- "如何用 Alembic 给 ENUM 加一个新值？" → `op.execute("ALTER TYPE session_status_enum ADD VALUE 'paused'")`，必须在事务外执行
- "PgBouncer 事务模式下 ENUM 兼容吗？" → 兼容，CREATE TYPE 是事务性 DDL，但在 PgBouncer 事务模式下需要 `DISABLE` 事务包装

---

### Q6: `ForeignKey ondelete='CASCADE'` 在生产环境中有什么风险？（考察：数据完整性 vs 安全删除）

**考察意图**：理解级联删除在生产环境中的取舍。

**答题思路**：从审计合规、误删恢复的角度切入。

**参考答案**：

```python
class AuditLog(BaseModelMixin, Base):
    session_id = mapped_column(Integer, ForeignKey("agent_sessions.id", ondelete="CASCADE"))
```

`ondelete='CASCADE'` 意味着删除 Session 时 PG 自动删除所有关联的审计日志和审批记录。这在管理会话生命周期时很方便（清理过期会话），但存在严肃风险：

1. **审计合规**：Agent 执行记录可能是合规要求必须保留的证据。一旦 Session 被误删，所有审计日志不可恢复
2. **级联风暴**：如果 Session 表关联了 10 张子表，一次 `DELETE` 可能产生 10 条级联删除，锁住大量行
3. **ORM 层的孤立对象**：SQLAlchemy session 中已有的 `AuditLog` 对象在级联删除后仍存在于内存中，访问其属性会触发懒加载 → `DetachedInstanceError`

**防范方案**：
- AuditLog 做**软删除**（用 `is_deleted` 标记，不真的 DELETE）
- 权限受限的 `DELETE` 操作（只有 admin 可以删除 Session，业务操作不允许）
- 设置 `ondelete='SET NULL'` 让外键字段为空而非删除整行

**延伸追问**：
- "PostgreSQL 的级联删除是在数据库层面还是 ORM 层面触发的？" → 数据库层面。`ondelete='CASCADE'` 是 DDL 约束，数据库在收到 DELETE 语句时自动执行。即使不经过 SQLAlchemy 直接 `psql -c "DELETE FROM agent_sessions WHERE id=1"`，级联也会触发
- "`SET NULL` 和 `CASCADE` 的选择策略？" → 子记录独立于父记录有意义的场景用 `SET NULL`（如审批记录即使会话删了也值得保留），子记录完全依赖于父记录的用 `CASCADE`（如日志条目）

---

### Q7: Alembic `--autogenerate` 是如何"发现"模型变更的？（考察：迁移工具的工作机制）

**考察意图**：理解 autogenerate 的"比较驱动"原理。

**答题思路**：从数据库当前状态 vs 代码模型元数据的 diff 过程展开。

**参考答案**：

`alembic revision --autogenerate` 的核心流程是 **两阶段比较**：

1. **获取当前数据库 schema**：连接数据库，通过 `inspect()` 读取所有表、列、索引、约束、ENUM 类型
2. **读取代码中 ORM 模型的 metadata**：遍历 `Base.metadata.tables` 中的所有 Table 对象
3. **逐表逐列 diff**：对比步骤 1 和步骤 2 的差异

diff 的结果是操作集合：
- 代码有但数据库没有 → `op.create_table()`
- 数据库有但代码没有 → `op.drop_table()`
- 两边都有但类型不同 → `op.alter_column()`
- 代码中 ENUM 有成员不在数据库中 → `op.execute("ALTER TYPE ... ADD VALUE")`

这就是为什么之前测试表出现在迁移脚本中（`_test_mixin` 等）——因为我们的集成测试先调用了 `Base.metadata.create_all()`，数据库中存在测试临时表，但代码的 models 目录中没有对应定义，Alembic 检测为"冗余 → 删除"。

**延伸追问**：
- "`--autogenerate` 能检测到索引的变更吗？" → 可以，但只检测 `index=True` 的单列索引，对复合索引（`__table_args__ = (Index(...),)`）不一定
- "检测不到哪些变更？" → 数据迁移（插入默认值）、权限变更（GRANT/REVOKE）、分区表变更。这些需要手动编写迁移脚本

---

### Q8: `sa.Enum('PENDING', 'RUNNING', ..., name='session_status_enum', create_constraint=True)` 中的 `create_constraint=True` 做了什么？（考察：SQLAlchemy ENUM 的参数语义）

**考察意图**：深入理解 SAEnum 和原生 PostgreSQL ENUM 的关系。

**答题思路**：从 `create_constraint` 的 false 和 true 两种行为的对比切入。

**参考答案**：

`create_constraint=True`（默认行为）告诉 SQLAlchemy 在数据库中创建一个**真正的 PostgreSQL ENUM 类型**（`CREATE TYPE session_status_enum AS ENUM('PENDING', 'RUNNING', ...)`），然后在建表时引用这个类型。

`create_constraint=False` 的行为完全不同：它在数据库层面用 **VARCHAR + CHECK 约束** 来模拟 ENUM：

```sql
-- create_constraint=False 时的 DDL
session_status VARCHAR(20) NOT NULL CHECK (session_status IN ('pending', 'running', ...))
```

这么做的好处是不需要创建独立的 TYPE 对象（TYPE 无法 ALTER 名字，删除表后 TYPE 仍然存在），但代价是 CHECK 约束的约束名在迁移中难以管理。

在我们的部署中，测试阶段创建的 `session_status_enum` TYPE 即使在 DROP TABLE 后仍然存在，需要用 `DROP TYPE ... CASCADE` 手动清理。

**延伸追问**：
- "`DROP TYPE ... CASCADE` 中的 CASCADE 做什么？" → 删除 TYPE 以及所有引用该 TYPE 的表列。如果不加 CASCADE，有列使用了该 TYPE 时会拒绝删除
- "ENUM TYPE 删除后，引用了它的列会变成什么类型？" → PostgreSQL 不允许存在"无类型"的列，所以 `DROP TYPE ... CASCADE` 会连同列一起删除。正确做法是 `ALTER COLUMN ... TYPE varchar`

---

## 四、Phase 2 测试覆盖总览

| 测试文件 | 数量 | 覆盖场景 |
|---------|------|---------|
| `test_database.py` | 9 | 引擎配置、Mixin 继承、依赖注入结构 |
| `test_models_integration.py` | 7 | 创建 Session、状态流转、Session-AuditLog‑Approval 关系、Schema 序列化 |
| **合计** | **16** | 全部通过 |

---

*文档生成日期：2026-05-26 | 作者：架构师 + Claude Code*
