"""
Redis 服务层：连接池管理、滑动窗口限流、状态缓存

架构设计：
  三个独立类，职责分离：
  1. RedisManager — 连接池生命周期 + 健康检查
  2. SlidingWindowRateLimiter — 基于 Sorted Set 的滑动窗口限流
  3. StateCache — Agent 运行态键值缓存（带 TTL）

依赖关系：
  SlidingWindowRateLimiter 和 StateCache 依赖 RedisManager.client，
  通过 FastAPI Depends 注入单例 RedisManager，确保全应用共享同一个连接池。
"""
import json
import logging
import secrets
import time
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)


# ====================================================================
# 1. RedisManager — 连接池管理器
# ====================================================================

class RedisManager:
    """
    Redis 异步连接池管理器

    职责：
      - 封装 redis.asyncio.Redis 客户端的创建和销毁
      - 提供健康检查 (ping)
      - 支持 async with 上下文管理器语法

    使用方式：
      redis_mgr = RedisManager(settings.redis_url)
      await redis_mgr.connect()
      await redis_mgr.ping()
      await redis_mgr.disconnect()
    """

    def __init__(
        self,
        redis_url: str = "",
        max_connections: int = 20,
    ) -> None:
        # 如果未显式传入 URL，则从全局配置读取
        self._redis_url = redis_url or settings.redis_url
        self._max_connections = max_connections or settings.REDIS_MAX_CONNECTIONS
        self._client: aioredis.Redis | None = None

    # ---- 属性：懒访问 client，避免未连接时使用 ----

    @property
    def client(self) -> aioredis.Redis:
        """
        获取 Redis 客户端实例

        Raises:
            RuntimeError: 如果尚未调用 connect()
        """
        if self._client is None:
            raise RuntimeError("Redis 客户端未连接，请先调用 connect() 方法初始化连接池")
        return self._client

    # ---- 核心生命周期方法 ----

    async def connect(self) -> None:
        """
        创建连接池并初始化 Redis 客户端

        关键设计：
          - 使用 redis.asyncio.ConnectionPool 管理连接复用，
            避免每次请求都创建新 TCP 连接（TCP 三次握手开销 ~1ms）
          - decode_responses=True 让 Redis 返回 str 而非 bytes，
            减少业务层手动 decode 的代码
        """
        try:
            pool = ConnectionPool.from_url(
                self._redis_url,
                max_connections=self._max_connections,
                decode_responses=True,  # 自动将 Redis 返回的 bytes 转换为 str
            )
            self._client = aioredis.Redis(connection_pool=pool)
            # 建立连接后立即做一次 ping，确保连接可用
            await self._client.ping()
            logger.info(
                "Redis 连接池已建立",
                extra={
                    "redis_url": self._redis_url.replace(
                        self._redis_url.split("@")[0].split("://")[-1]
                        if "@" in self._redis_url
                        else "",
                        "***",
                    ),
                    "max_connections": self._max_connections,
                },
            )
        except Exception as exc:
            logger.error("Redis 连接失败: %s", exc)
            raise

    async def disconnect(self) -> None:
        """
        优雅关闭连接池

        内部逻辑：
          redis.asyncio.Redis.close() 会先等待所有进行中的命令完成，
          再关闭 TCP 连接，不会造成命令截断。
        """
        if self._client is not None:
            try:
                await self._client.aclose()  # aclose() 等价于 close() + await
                logger.info("Redis 连接池已释放")
            except Exception as exc:
                logger.error("Redis 关闭连接池时发生异常: %s", exc)
            finally:
                self._client = None

    # ---- 便捷方法 ----

    async def ping(self) -> bool:
        """健康检查：向 Redis 发送 PING 命令，验证连接存活"""
        try:
            result = await self.client.ping()
            return result is True
        except Exception as exc:
            logger.error("Redis PING 失败: %s", exc)
            return False

    async def close(self) -> None:
        """
        disconnect 的别名，便于和 FastAPI 生命周期事件对接
        app.add_event("shutdown", redis_manager.close)
        """
        await self.disconnect()

    # ---- 上下文管理器：支持 async with 语法 ----

    async def __aenter__(self) -> "RedisManager":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()


# ====================================================================
# 2. SlidingWindowRateLimiter — 滑动窗口限流器
# ====================================================================

class SlidingWindowRateLimiter:
    """
    基于 Redis Sorted Set 的滑动窗口限流器

    算法原理（Sliding Window Log）：
      1. 每个请求以当前时间戳为 score 加入 ZSET
      2. 统计前先删除窗口外的旧记录（ZREMRANGEBYSCORE）
      3. 统计窗口内剩余记录数（ZCARD）
      4. 若 < 上限 → 允许；否则 → 拒绝并返回 retry_after

    为什么不用固定窗口？
      固定窗口（如每分钟 100 次）存在边界突发问题：
      第 0:59 发 100 次 + 第 1:01 发 100 次 = 2 秒内 200 次
      滑动窗口消除了这个漏洞，任意连续时间窗口内都不会超限。

    使用方式：
      limiter = SlidingWindowRateLimiter(redis_manager)
      allowed, remaining, retry_after = await limiter.check("user:123:api/login", 10, 60)
    """

    # Redis key 前缀，避免与其他业务的 key 冲突
    KEY_PREFIX = "ratelimit"

    def __init__(self, redis_manager: RedisManager) -> None:
        self._redis = redis_manager

    # ---- 核心限流方法 ----

    async def check(
        self,
        identifier: str,
        max_requests: int = 10,
        window_seconds: int = 60,
    ) -> tuple[bool, int, float]:
        """
        检查是否允许本次请求

        Args:
            identifier:   唯一标识（如 "user:123" 或 "ip:192.168.1.1:/api/login"）
            max_requests: 窗口内最大允许请求数
            window_seconds: 时间窗口长度（秒）

        Returns:
            (allowed, remaining, retry_after)
            - allowed:      True=放行, False=拒绝
            - remaining:    窗口内剩余配额（被拒绝时为 0）
            - retry_after:  距离下一个可用名额的秒数（allowed=True 时为 0.0）
        """
        key = f"{self.KEY_PREFIX}:{identifier}"
        now = time.monotonic()
        window_start = now - window_seconds

        try:
            # 使用 pipeline 保证原子性：3 条命令要么全执行要么全不执行
            async with self._redis.client.pipeline(transaction=True) as pipe:
                # 步骤1: 删除窗口外的过期记录
                pipe.zremrangebyscore(key, 0, window_start)
                # 步骤2: 统计窗口内当前请求数
                pipe.zcard(key)
                # 执行前两步
                _, current_count = await pipe.execute()
                # 提取 ZCARD 的返回值
                current_count = int(current_count)

            # 步骤3: 判断是否允许本次请求
            if current_count < max_requests:
                # 放行：将本次请求加入 ZSET
                member = f"{now}:{secrets.token_hex(8)}"  # secrets.token_hex 保证全局唯一，避免 ZSET member 碰撞（ZADD 遇相同 member 会更新 score 而非新增）
                async with self._redis.client.pipeline(transaction=True) as pipe:
                    pipe.zadd(key, {member: now})
                    # 设置 key 的过期时间 = 窗口长度，自动清理冷数据
                    pipe.expire(key, window_seconds)
                    await pipe.execute()

                remaining = max_requests - current_count - 1
                return True, remaining, 0.0
            else:
                # 拒绝：计算重试时间
                # 找到窗口中最早的一条记录，该记录过期时间 = retry_after
                oldest = await self._redis.client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    oldest_score = oldest[0][1]  # score = timestamp
                    retry_after = oldest_score + window_seconds - now
                    # retry_after 不应为负数（理论上不会，做防御性保护）
                    retry_after = max(retry_after, 0.0)
                else:
                    retry_after = 0.0

                return False, 0, retry_after

        except Exception as exc:
            # 降级策略：Redis 不可用时默认放行，避免限流组件成为单点故障
            logger.error("滑动窗口限流检查失败，降级放行: identifier=%s, error=%s", identifier, exc)
            return True, max_requests, 0.0

    # ---- 便捷方法 ----

    async def reset(self, identifier: str) -> None:
        """重置某个标识符的限流计数（用于测试或手动解锁）"""
        key = f"{self.KEY_PREFIX}:{identifier}"
        try:
            await self._redis.client.delete(key)
        except Exception as exc:
            logger.error("重置限流计数失败: identifier=%s, error=%s", identifier, exc)

    async def get_current_count(self, identifier: str) -> int:
        """查询某个标识符当前窗口内的请求数（用于监控/调试）"""
        key = f"{self.KEY_PREFIX}:{identifier}"
        try:
            return await self._redis.client.zcard(key)
        except Exception as exc:
            logger.error("查询限流计数失败: identifier=%s, error=%s", identifier, exc)
            return 0


# ====================================================================
# 3. StateCache — Agent 运行态键值缓存
# ====================================================================

class StateCache:
    """
    Agent 运行态状态缓存

    用途：
      - 缓存 LangGraph Agent 的当前状态（AgentState Pydantic 模型）
      - 会话元数据（session 创建时间、当前步骤、LLM 调用次数）
      - 短期 TTL 数据（默认 30 分钟，超时自动清除）

    序列化策略：
      - 写入：Pydantic 模型 → model_dump_json() → Redis String
      - 读取：Redis String → json.loads() → 原始 dict（由调用方决定如何还原为模型）
      - 选择 JSON 而非 pickle：跨语言兼容、可读、安全（无 RCE 风险）

    使用方式：
      cache = StateCache(redis_manager)
      await cache.set("session:abc123", agent_state.model_dump(), ttl=1800)
      data = await cache.get("session:abc123")
    """

    KEY_PREFIX = "state"

    def __init__(self, redis_manager: RedisManager) -> None:
        self._redis = redis_manager

    # ---- 基础 CRUD ----

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl: int = 1800,  # 默认 30 分钟
    ) -> bool:
        """
        写入缓存

        Args:
            key:   缓存键（会自动加上 state: 前缀）
            value: 要缓存的字典数据（通常是 Pydantic model_dump() 的结果）
            ttl:   过期时间（秒），0 表示永不过期（不推荐）

        Returns:
            True 表示写入成功
        """
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            serialized = json.dumps(value, ensure_ascii=False)
            if ttl > 0:
                await self._redis.client.setex(full_key, ttl, serialized)
            else:
                await self._redis.client.set(full_key, serialized)
            return True
        except Exception as exc:
            logger.error("StateCache 写入失败: key=%s, error=%s", key, exc)
            return False

    async def get(self, key: str) -> dict[str, Any] | None:
        """
        读取缓存

        Returns:
            反序列化后的字典，key 不存在或异常时返回 None
        """
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            raw = await self._redis.client.get(full_key)
            if raw is None:
                return None
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("StateCache JSON 解析失败: key=%s, error=%s", key, exc)
            return None
        except Exception as exc:
            logger.error("StateCache 读取失败: key=%s, error=%s", key, exc)
            return None

    async def delete(self, key: str) -> bool:
        """删除缓存键"""
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            deleted = await self._redis.client.delete(full_key)
            return deleted > 0
        except Exception as exc:
            logger.error("StateCache 删除失败: key=%s, error=%s", key, exc)
            return False

    async def exists(self, key: str) -> bool:
        """检查缓存键是否存在"""
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            return await self._redis.client.exists(full_key) > 0
        except Exception as exc:
            logger.error("StateCache 存在性检查失败: key=%s, error=%s", key, exc)
            return False

    # ---- 批量操作 ----

    async def mget(self, keys: list[str]) -> dict[str, dict[str, Any] | None]:
        """
        批量读取缓存

        Returns:
            {original_key: parsed_value_or_None} 字典
        """
        full_keys = [f"{self.KEY_PREFIX}:{k}" for k in keys]
        result: dict[str, dict[str, Any] | None] = {}
        try:
            raw_list = await self._redis.client.mget(full_keys)
            for original_key, raw in zip(keys, raw_list):
                if raw is None:
                    result[original_key] = None
                else:
                    try:
                        result[original_key] = json.loads(raw)
                    except json.JSONDecodeError:
                        result[original_key] = None
            return result
        except Exception as exc:
            logger.error("StateCache 批量读取失败: keys=%s, error=%s", keys, exc)
            return {k: None for k in keys}

    async def mset(
        self,
        mapping: dict[str, dict[str, Any]],
        ttl: int = 1800,
    ) -> bool:
        """
        批量写入缓存（注意：Redis MSET 不支持 TTL，
        本方法使用 pipeline 批量 SETEX，保证原子性不如 MSET 但支持 TTL）
        """
        try:
            async with self._redis.client.pipeline(transaction=False) as pipe:
                for key, value in mapping.items():
                    full_key = f"{self.KEY_PREFIX}:{key}"
                    serialized = json.dumps(value, ensure_ascii=False)
                    if ttl > 0:
                        pipe.setex(full_key, ttl, serialized)
                    else:
                        pipe.set(full_key, serialized)
                await pipe.execute()
            return True
        except Exception as exc:
            logger.error("StateCache 批量写入失败: keys=%s, error=%s", list(mapping.keys()), exc)
            return False

    # ---- TTL 管理 ----

    async def get_ttl(self, key: str) -> int:
        """
        查询键的剩余过期时间

        Returns:
            剩余秒数，-1 表示永不过期，-2 表示 key 不存在
        """
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            return await self._redis.client.ttl(full_key)
        except Exception as exc:
            logger.error("StateCache 查询 TTL 失败: key=%s, error=%s", key, exc)
            return -2

    async def expire(self, key: str, ttl: int) -> bool:
        """更新键的过期时间"""
        full_key = f"{self.KEY_PREFIX}:{key}"
        try:
            return await self._redis.client.expire(full_key, ttl) > 0
        except Exception as exc:
            logger.error("StateCache 更新 TTL 失败: key=%s, error=%s", key, exc)
            return False
