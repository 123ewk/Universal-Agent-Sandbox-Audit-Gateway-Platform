# Phase 3 — Redis 服务层 技术文档与面试八股文

---

## 一、设计动机

### 为什么需要独立的 Redis 服务层？

在 Universal Agent Sandbox 平台中，Redis 承担三个关键角色：

1. **限流器**：对外暴露的 API 需要防止滥用。大模型 API 调用成本高（GPT-4o 约 $5/1M input tokens），不限流可能导致账单爆炸。
2. **状态缓存**：LangGraph Agent 执行过程中需要暂存中间状态（Plan → Execute → Reflect 的状态流转），这些数据不需要持久化，但需要亚毫秒级读写。
3. **Pub/Sub**：后续 WebSocket 推流（Phase 6）需要跨进程广播 Agent 操作日志，Redis Pub/Sub 是轻量级方案。

### 设计原则

- **职责分离**：三个独立类（RedisManager / SlidingWindowRateLimiter / StateCache），各管各的
- **降级优先**：Redis 不可用时，限流器选择放行（fail-open），缓存选择返回 None，绝不阻塞主业务
- **连接复用**：全应用共享同一个连接池，避免每次请求都创建新 TCP 连接

---

## 二、源码逐行解读

### 2.1 RedisManager — 连接池管理器

```python
class RedisManager:
    def __init__(self, redis_url: str = "", max_connections: int = 20) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._max_connections = max_connections or settings.REDIS_MAX_CONNECTIONS
        self._client: aioredis.Redis | None = None
```

**关键设计**：`redis_url or settings.redis_url` — 允许测试时传入独立 URL，生产环境从全局配置读取。这叫"依赖注入优先，默认值兜底"。

```python
    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("Redis 客户端未连接，请先调用 connect() 方法初始化连接池")
        return self._client
```

**为什么用 property 而非直接暴露 `_client`？**
- `property` 可以加保护逻辑（未连接时抛出明确的 RuntimeError 而非晦涩的 NoneType error）
- 如果直接暴露 `_client`，调用方写 `redis_manager._client.get("key")` 会在未连接时得到 `AttributeError: 'NoneType' object has no attribute 'get'`，排查困难

```python
            pool = ConnectionPool.from_url(
                self._redis_url,
                max_connections=self._max_connections,
                decode_responses=True,
            )
```

**`decode_responses=True` 的底层原理**：
Redis 协议传输的是 RESP（REdis Serialization Protocol）格式的字节流。默认情况下，redis-py 返回 `bytes` 类型。设置 `decode_responses=True` 后，客户端会自动将响应 decode 为 `str`。代价是：如果存储的是二进制数据（如 pickle），会被错误 decode。本项目只用 JSON，所以设为 True。

### 2.2 SlidingWindowRateLimiter — 滑动窗口限流器

```
算法原理（Sliding Window Log）：

时间轴 ──────────────────────────────────────────>
         │         窗口 (60s)       │
         │  [req1] [req2] [req3]   │  [req4] → 检查
         │                         │
         └─── window_start ────────┘─── now
              (now - 60s)

步骤：
1. ZREMRANGEBYSCORE key 0 window_start   → 删除窗口外旧记录
2. ZCARD key                              → 统计窗口内当前请求数
3. if count < max: ZADD + 放行
   else: 拒绝 + 计算 retry_after
```

**为什么分两步 pipeline？**

```python
# Pipeline 1: 读操作（查询当前计数）
async with self._redis.client.pipeline(transaction=True) as pipe:
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    _, current_count = await pipe.execute()

# Pipeline 2: 写操作（新增成员）
async with self._redis.client.pipeline(transaction=True) as pipe:
    pipe.zadd(key, {member: now})
    pipe.expire(key, window_seconds)
    await pipe.execute()
```

**为什么不是一次 pipeline？** 因为需要根据 `current_count` 的值（查询结果）来决定是否执行写入。Redis pipeline 的 `transaction=True` 模式使用 `MULTI/EXEC`，所有命令都在 EXEC 时一次性执行，无法在中间插入 Python 的条件判断。

> **高并发下的 TOCTOU 问题**：Pipeline 1 和 Pipeline 2 之间，另一个请求可能也读到了 `current_count=9`，两个请求都认为自己可以放行，实际放行了 11 个。这是"先检查后执行"(check-then-act) 的经典竞态。生产级解决方案是 Lua 脚本（见面试题 5）。

**member 唯一性保证**：

```python
member = f"{now}:{secrets.token_hex(8)}"
```

`secrets.token_hex(8)` 从操作系统熵源（`/dev/urandom` 或 Windows `CryptGenRandom`）读取 8 字节随机数，转为 16 字符十六进制字符串。碰撞概率 ≈ `1 / 2^64`，远小于宇宙射线导致的内存 bit flip 概率。

**为什么不用 `id(object())`？（实际踩坑）**
CPython 使用引用计数 GC。`id(object())` 创建的临时对象在表达式结束后立即被回收，下一轮循环的 `object()` 可能复用同一内存地址 → `id()` 返回值相同 → ZSET member 相同 → `ZADD` 变成更新而非新增 → 计数不增长。

### 2.3 StateCache — Agent 运行态缓存

```python
async def set(self, key: str, value: dict[str, Any], ttl: int = 1800) -> bool:
    full_key = f"{self.KEY_PREFIX}:{key}"
    serialized = json.dumps(value, ensure_ascii=False)
    if ttl > 0:
        await self._redis.client.setex(full_key, ttl, serialized)
    else:
        await self._redis.client.set(full_key, serialized)
```

**为什么选 JSON 而非 pickle？**
| 维度 | JSON | pickle |
|------|------|--------|
| 安全性 | 无代码执行风险 | 可被注入恶意 pickle 实现 RCE |
| 跨语言 | 任何语言可读 | 仅 Python |
| 可读性 | 人类可读 | 二进制 |
| 性能 | 较快（C 扩展） | 较快 |
| 类型支持 | 基础类型 + 嵌套 | 任意 Python 对象 |

本项目缓存的是 Pydantic 模型的 `model_dump()` 结果（dict），JSON 完美适配。

`ensure_ascii=False` — 允许直接写入中文字符而非 `\uXXXX` 转义序列，节省存储空间且 Redis 中可读。

**TTL 默认 1800 秒（30 分钟）**：Agent 任务最长执行时间通常不超过 30 分钟，超时的 session 自动清理。

---

## 三、关键技术原理

### 3.1 Redis Pipeline 与 MULTI/EXEC

```
普通模式（3 次 RTT）：
Client → Server: ZREMRANGEBYSCORE key 0 100
Client ← Server: 3
Client → Server: ZCARD key
Client ← Server: 5
Client → Server: ZADD key {member: score}
Client ← Server: 1
总耗时：3 × RTT（每次 ~0.5ms）= 1.5ms

Pipeline 模式（1 次 RTT）：
Client → Server: ZREMRANGEBYSCORE key 0 100
                 ZCARD key
                 ZADD key {member: score}
Client ← Server: 3, 5, 1
总耗时：1 × RTT = 0.5ms
```

**关键点**：Pipeline 是客户端优化（批量发送/接收），不是服务器端的事务。加上 `transaction=True` 才是真正的 Redis 事务（MULTI/EXEC），保证中间不会被其他客户端命令插入。

### 3.2 Redis 连接池原理

```
不使用连接池：
每个请求 → 创建 TCP 连接 (TCP 三次握手 ~1ms + Redis AUTH ~0.1ms)
         → 执行命令 (~0.3ms)
         → 销毁 TCP 连接 (TCP 四次挥手 ~0.5ms)
开销：连接建立/销毁占执行时间的 5 倍以上

使用连接池：
应用启动 → 预创建 10 个 TCP 连接放入池中
请求到来 → 从池中借一个连接 → 执行命令 → 归还连接
         → 连接保持 TCP keepalive，无需重复握手
```

`ConnectionPool.from_url()` 内部使用 `redis.asyncio.ConnectionPool`，它是线程安全/协程安全的。连接池满了（所有连接被占用）时，新请求排队等待，等待超时后抛 `ConnectionError`。

### 3.3 Redis 过期键删除策略

Redis 使用两种策略混合：

1. **惰性删除 (Lazy Expiration)**：访问 key 时检查是否过期，过期则删除并返回 nil。CPU 友好但可能留下垃圾。
2. **定期删除 (Active Expiration)**：每 100ms 随机抽样 20 个设置了 TTL 的 key，删除其中过期的。如果过期比例 > 25%，继续抽样。

**对本项目的影响**：TTL 设置为 1 秒的测试 key 可能不会在 1.000 秒后立即删除（取决于定期删除的时机），所以测试中用 `asyncio.sleep(1.5)` 留出缓冲。

---

## 四、面试高频问题

---

### 面试题 1：固定窗口 vs 滑动窗口限流，区别是什么？

**考察意图**：理解限流算法的演进和各自缺陷

**答题思路**：先描述固定窗口的原理，再指出其"边界突发"问题，最后解释滑动窗口如何解决

**参考答案**：

固定窗口计数器：将时间划分为固定窗口（如每分钟），每个窗口内计数，超限拒绝。

**边界突发问题**：
```
窗口 1 (0:00-1:00)：100 个请求在第 59 秒发完 → 全部放行
窗口 2 (1:00-2:00)：100 个请求在第 1 秒发完 → 全部放行
结果：2 秒内实际放行了 200 个请求！
```

滑动窗口（本项目）：使用 Redis ZSET 存储每个请求的精确时间戳，检查时只统计当前时间往前推 N 秒内的记录数。任意时间点统计的窗口内请求数都不会超过阈值。

**代价**：滑动窗口需要存储每条请求记录（而非一个计数器），内存占用更高，但随着窗口过期自动清除（ZREMRANGEBYSCORE + EXPIRE），内存可控。

**延伸追问**：还有哪些限流算法？
- **令牌桶 (Token Bucket)**：以恒定速率向桶中放入令牌，请求需获取令牌才能执行，允许突发（桶容量 = 可积攒的令牌数）
- **漏桶 (Leaky Bucket)**：请求进入桶，以恒定速率"漏出"处理，强制平滑流量

---

### 面试题 2：Redis Pipeline 的 transaction=True 和 MULTI/EXEC 是什么关系？

**考察意图**：理解 Redis 事务的本质（与关系型数据库事务的区别）

**答题思路**：先讲 MULTI/EXEC 的语义，再讲 redis-py 中 pipeline 如何映射到 MULTI/EXEC，最后指出 Redis 事务的"非原子性"特点

**参考答案**：

Redis 的 MULTI/EXEC 提供命令的**隔离性**（Isolation），但不提供**原子性回滚**（Atomicity）。

```
MULTI         # 开启事务
SET key1 "a"
INCR key2     # 假设 key2 是 string 类型，INCR 会报错
SET key3 "b"
EXEC          # 执行
```

结果：`SET key1` 成功，`INCR key2` 报错（类型错误），`SET key3` **仍然会执行**，不会因为中间的命令失败而回滚。这与 MySQL 的 `ROLLBACK` 完全不同。

redis-py 中：
- `pipeline(transaction=True)` → 内部使用 MULTI/EXEC，所有命令缓冲后一次发送
- `pipeline(transaction=False)` → 不使用 MULTI/EXEC，命令仍然批量发送但之间可能被其他客户端命令插入

**为什么本项目"先查后写"分两次 pipeline？** 因为需要根据查询结果（`current_count`）做 Python 层的条件判断（`if current_count < max_requests`），在 Pipeline 内部无法实现这种依赖。

**延伸追问**：如何解决"先查后写"的竞态问题？
→ Lua 脚本（见面试题 5）

---

### 面试题 3：`time.monotonic()` 和 `time.time()` 有什么区别？为什么限流器用 monotonic？

**考察意图**：理解 Python 时间函数的底层差异及适用场景

**答题思路**：先讲两个函数各自获取的是什么时间，再讲 `time.time()` 的"回拨"问题

**参考答案**：

| 属性 | `time.time()` | `time.monotonic()` |
|------|--------------|-------------------|
| 含义 | Unix 时间戳（自 1970-01-01 起秒数） | 单调递增时钟（任意参考点） |
| 受 NTP 校时影响 | **是**（可能回拨） | **否** |
| 受手动改时间影响 | **是** | **否** |
| 绝对值意义 | 有意义（可转为日期） | 无意义（只对差值有意义） |
| 精度 | 微秒级 | 微秒级 |

**时间回拨的经典事故**：
```
1. time.time() = 1000.0
2. NTP 校时发现本地时钟快了 5 秒，调整系统时钟
3. time.time() = 995.0  ← 时间"倒退"了！

限流器计算：
  window_start = now - window_seconds = 995.0 - 60 = 935.0
  ZREMRANGEBYSCORE key 0 935.0
  → 删除了 score 在 935-1000 区间的有效记录！
  → 窗口计数变少 → 应该拒绝的请求被放行
```

`time.monotonic()` 保证永远不后退，适合所有需要计算"时间间隔"的场景（限流、超时、性能测量）。

**延伸追问**：`time.perf_counter()` 又是什么？
→ `perf_counter()` 也是单调递增，但精度更高（纳秒级），且包含 sleep 时间。适合性能基准测试（benchmark）。`monotonic()` 精度略低但不包含系统挂起时间。

---

### 面试题 4：`decode_responses=True` 的原理是什么？为什么不直接用默认的 bytes？

**考察意图**：理解 Redis 协议（RESP）的编码层和 redis-py 的数据转换层

**答题思路**：从 RESP 协议讲起，再到 redis-py 的编码/解码流程

**参考答案**：

Redis 使用 RESP（REdis Serialization Protocol）协议通信，所有数据以 `\r\n` 分隔的文本帧传输：

```
Client →: GET foo\r\n
Server ←: $3\r\nbar\r\n     ← "$3" 表示接下来 3 字节，"bar" 是值
```

`$3\r\nbar\r\n` 在网络上就是 9 个字节的 bytes。redis-py 的解析器收到这 9 个字节后：

- **`decode_responses=False`（默认）**：返回 `b"bar"` — 原始 bytes
- **`decode_responses=True`**：调用 `.decode("utf-8")` → 返回 `"bar"` — Python str

实现原理：redis-py 内部有一个 `PythonParser` 类，在解析 RESP 响应时：
```python
# redis-py 内部简化逻辑
if self.decode_responses:
    return response.decode("utf-8")
return response  # bytes
```

**代价**：
1. 如果存储的是二进制数据（如图片、protobuf），强行 decode 会得到乱码或 UnicodeDecodeError
2. decode 有 CPU 开销（虽然极小）

本项目只存 JSON 文本，所以设为 True，让业务代码干净。

---

### 面试题 5：如何用 Lua 脚本解决限流器的 TOCTOU 竞态？

**考察意图**：理解 Redis Lua 脚本的原子性保证及其在限流场景的应用

**答题思路**：先讲当前 Python 代码的竞态窗口在哪，再写出 Lua 脚本，最后解释为什么 Lua 脚本是原子的

**参考答案**：

**当前 Python 代码的竞态窗口**：
```
请求 A                      请求 B                       Redis
  │                          │                            │
  ├─ Pipeline 1 (读) ────────┼────────────────────────────┤ count=9
  │                          ├─ Pipeline 1 (读) ──────────┤ count=9 ← 也是 9！
  │                          │                            │
  ├─ Pipeline 2 (写) ────────┼────────────────────────────┤ count=10
  │                          ├─ Pipeline 2 (写) ──────────┤ count=11 ← 超限！
```

**Lua 脚本方案**：

```lua
-- ratelimit.lua
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

-- 1. 清理窗口外记录
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

-- 2. 统计窗口内当前请求数
local current = redis.call('ZCARD', key)

-- 3. 判断 + 写入
if current < max_requests then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window)
    return {1, max_requests - current - 1, 0}  -- 放行
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 0
    if #oldest > 0 then
        retry_after = oldest[2] + window - now
    end
    return {0, 0, retry_after}  -- 拒绝
end
```

**为什么 Lua 脚本是原子的？**
Redis 执行 Lua 脚本时，整个脚本作为一个原子操作：脚本执行期间，Redis 不会处理任何其他客户端命令（单线程模型）。相当于把"查询 + 判断 + 写入"打包成了一个不可分割的操作。

**代价**：Lua 脚本执行期间会阻塞 Redis 主线程，如果脚本执行时间过长（如操作大 key），其他所有请求都会排队等待。Redis 官方建议单个 Lua 脚本执行时间 < 1 秒。

**Python 调用**：
```python
result = await redis.eval(
    lua_script,
    1,  # KEYS 数量
    key, now, window_seconds, max_requests, member
)
```

---

### 面试题 6：Redis 连接池的 max_connections 和 pool_recycle 怎么配置？

**考察意图**：生产级 Redis 连接池的调优经验

**答题思路**：先讲 max_connections 的计算公式，再讲 pool_recycle（或 idle 超时）的必要性

**参考答案**：

**max_connections 配置公式**：
```
max_connections = 并发请求数 × 单次请求平均 Redis 命令数 / 2
```

- 假设并发请求 100，每个请求平均 5 次 Redis 命令，单次命令 ~0.3ms
- 一个连接在 1ms 内可服务 ~3 次命令 → 可服务一个请求的 Redis 调用
- 但连接是可以复用的（请求间共享），不是每个请求独占一个连接
- 经验值：`并发数 / 4` 到 `并发数 / 2`

本项目：开发阶段 20 个连接，单机 FastAPI 通常处理不超过 100 并发请求，足够。

**为什么需要 pool_recycle（连接回收）？**

云环境（K8s/Docker）中，TCP 连接可能被中间网络设备（NAT 网关、负载均衡器、防火墙）静默断开：
```
Redis Client ─── TCP ─── NAT 网关 ─── Redis Server
                          │
                    NAT 30 分钟无数据后删除映射表
                    客户端不知道，TCP 连接还存在（无数据交互无法检测）
                    下次发送命令时发现对端已经不认识这个连接
                    → ConnectionResetError
```

**解决方案**：
- `pool_recycle`（本项目）：连接使用超过 N 秒后自动关闭重建。本项目设 3600 秒（1 小时）
- Redis 的 `timeout` 配置：服务端主动关闭空闲连接
- TCP keepalive：操作系统层面定期发送探测包

---

### 面试题 7：在限流器中为什么 Redis 挂了要"放行"而非"拒绝"？

**考察意图**：分布式系统中降级策略的设计哲学

**答题思路**：先讲 fail-open 和 fail-close 的概念，再结合业务场景分析

**参考答案**：

这是**可用性 vs 安全性**的经典权衡：

| 策略 | Redis 不可用时 | 后果 |
|------|-------------|------|
| **fail-open（放行）** | 所有请求通过 | 可能被 DDoS，但服务可用 |
| **fail-close（拒绝）** | 所有请求被拒 | 服务完全不可用，用户体验灾难 |

**选择 fail-open 的理由**：
1. 限流是**辅助性**防护，不是核心业务逻辑。即使限流失效，后端还有 API 限流、操作系统资源限制等多层防护
2. Redis 故障通常持续时间短（几秒到几分钟），短暂失去限流保护的风险远小于服务完全不可用的风险
3. 用户体验优先：宁可让正常用户通过也不要把所有人都挡在外面

**`try-except` 降级代码**：
```python
except Exception as exc:
    logger.error("滑动窗口限流检查失败，降级放行: identifier=%s, error=%s", identifier, exc)
    return True, max_requests, 0.0  # 放行
```

**Fail-close 适用于什么场景？**
- 支付/转账：宁可拒绝也要保证资金安全
- 权限校验：未通过鉴权的一律拒绝
- 安全关键系统：核电站控制、医疗设备等

---

### 面试题 8：`secrets.token_hex()` 和 `uuid.uuid4()` 有什么区别？什么时候用哪个？

**考察意图**：理解 Python 中不同随机性来源的差异

**答题思路**：从熵源、长度、使用场景三个维度对比

**参考答案**：

| 维度 | `secrets.token_hex(8)` | `uuid.uuid4().hex` |
|------|----------------------|-------------------|
| **熵源** | 操作系统 CSPRNG（`/dev/urandom`） | 操作系统 CSPRNG（Python 3.7+） |
| **输出长度** | 可变（参数控制） | 固定 32 hex = 128 bits |
| **碰撞概率** | `1/2^(n×4)`，n=8 → 1/2^64 | `1/2^122`（6 位用于版本/变体） |
| **性能** | 2 次系统调用 | 2 次系统调用 |
| **可读性** | 纯随机十六进制 | 含固定位（如第 13 位固定为 '4'） |

**使用场景判断**：
- 需要短 token（16 hex = 64 bit）→ `secrets.token_hex(8)`，碰撞概率远低于项目所需
- 需要全局唯一 ID → `uuid.uuid4()`，128 bit 碰撞概率可忽略
- 并行生成且要求唯一 → 两者都能保证，UUID 更保守

**本项目为什么用 `secrets.token_hex` 而非 uuid？**
ZSET member 需要尽量短（每个 member 占用内存），64 bit 的碰撞概率（~10^-10 对 10 万请求/天）已足够，不需要 122 bit 的 UUID。

**安全提醒**：禁止用 `random` 模块（Mersenne Twister，非密码学安全）生成 token/密钥。`random.getrandbits()` 输出可被预测。

---

## 五、测试覆盖

| 测试套件 | 测试数 | 覆盖范围 |
|----------|--------|---------|
| TestRedisManager | 8 | connect/ping/disconnect/上下文管理器/重连/未连接保护 |
| TestSlidingWindowRateLimiter | 7 | 基本限流/超限拒绝/独立计数/reset/窗口过期/当前计数 |
| TestStateCache | 16 | CRUD/TTL/批量操作/复杂类型/中文/边界情况 |
| **合计** | **31** | **全部通过** |

---

## 六、文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/services/__init__.py` | 1 | 模块标记 |
| `app/services/redis_service.py` | 445 | RedisManager + SlidingWindowRateLimiter + StateCache |
| `tests/test_redis_service.py` | 300 | 31 项测试 |
