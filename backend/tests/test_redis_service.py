"""
Phase 3 — Redis 服务层测试套件

测试范围：
  1. RedisManager: 连接 / 断开 / ping / 上下文管理器 / 未连接访问保护
  2. SlidingWindowRateLimiter: 基本限流 / 窗口滑动 / reset / 降级策略
  3. StateCache: CRUD / TTL / 批量操作 / JSON 序列化

测试策略：
  - 全部使用真实 Redis（127.0.0.1:6379），不使用 mock
  - 每个测试使用独立 key 前缀避免数据污染
  - test_redis_connection 作为前置依赖测试（pre-test），
    如果 Redis 连不上 → skip 后续所有测试
"""
import asyncio
import time

import pytest

from app.services.redis_service import (
    RedisManager,
    SlidingWindowRateLimiter,
    StateCache,
)

# ====================================================================
# 前置检查：Redis 可用性
# ====================================================================

REDIS_URL = "redis://127.0.0.1:6379/0"


@pytest.fixture
def redis_url():
    """返回 Redis 连接 URL 供测试中使用"""
    return REDIS_URL


@pytest.fixture
async def redis_manager():
    """函数级 RedisManager 实例"""
    mgr = RedisManager(redis_url=REDIS_URL)
    await mgr.connect()
    yield mgr
    await mgr.disconnect()


async def test_redis_connection(redis_manager):
    """前置验证：Redis 必须可达，否则后续测试全部 skip"""
    result = await redis_manager.ping()
    assert result is True, f"Redis 不可达，请确保 Redis 服务已启动: {REDIS_URL}"


# ====================================================================
# Test Suite 1: RedisManager
# ====================================================================


class TestRedisManager:
    """RedisManager 连接池管理器测试"""

    async def test_connect_and_ping(self, redis_manager):
        """验证：连接成功后 ping 返回 True"""
        result = await redis_manager.ping()
        assert result is True

    async def test_client_property_returns_client(self, redis_manager):
        """验证：连接后 client 属性返回 Redis 实例"""
        client = redis_manager.client
        assert client is not None

    async def test_client_raises_before_connect(self, redis_url):
        """验证：未连接时访问 client 属性抛出 RuntimeError"""
        mgr = RedisManager(redis_url=redis_url)
        with pytest.raises(RuntimeError, match="未连接"):
            _ = mgr.client

    async def test_disconnect_clears_client(self, redis_url):
        """验证：disconnect 后 client 置为 None"""
        mgr = RedisManager(redis_url=redis_url)
        await mgr.connect()
        assert mgr._client is not None
        await mgr.disconnect()
        assert mgr._client is None

    async def test_disconnect_idempotent(self, redis_url):
        """验证：重复调用 disconnect 不会抛异常"""
        mgr = RedisManager(redis_url=redis_url)
        await mgr.connect()
        await mgr.disconnect()
        await mgr.disconnect()  # 第二次调用应该安全
        assert mgr._client is None

    async def test_context_manager(self, redis_url):
        """验证：async with 语法自动管理连接生命周期"""
        async with RedisManager(redis_url=redis_url) as mgr:
            assert mgr._client is not None
            assert await mgr.ping() is True
        # 退出上下文后 client 应为 None
        assert mgr._client is None

    async def test_reconnect_after_disconnect(self, redis_url):
        """验证：断开后可以重新连接"""
        mgr = RedisManager(redis_url=redis_url)
        await mgr.connect()
        await mgr.disconnect()
        await mgr.connect()
        assert await mgr.ping() is True
        await mgr.disconnect()

    async def test_close_alias(self, redis_url):
        """验证：close() 是 disconnect() 的有效别名"""
        mgr = RedisManager(redis_url=redis_url)
        await mgr.connect()
        await mgr.close()
        assert mgr._client is None


# ====================================================================
# Test Suite 2: SlidingWindowRateLimiter
# ====================================================================


class TestSlidingWindowRateLimiter:
    """滑动窗口限流器测试"""

    @pytest.fixture(autouse=True)
    async def cleanup(self, redis_manager):
        """每个测试前后清理限流 key"""
        yield
        # 清理所有测试中可能创建的 key
        keys = await redis_manager.client.keys("ratelimit:*")
        if keys:
            await redis_manager.client.delete(*keys)

    async def test_first_request_allowed(self, redis_manager):
        """验证：第一条请求必然放行"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        allowed, remaining, retry = await limiter.check("test:first", max_requests=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4
        assert retry == 0.0

    async def test_requests_within_limit(self, redis_manager):
        """验证：窗口内请求数未达上限时全部放行"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        for i in range(5):
            allowed, remaining, retry = await limiter.check("test:within", max_requests=5, window_seconds=60)
            assert allowed is True, f"第 {i+1} 次请求应被放行"
            assert remaining == 4 - i

    async def test_exceeds_limit(self, redis_manager):
        """验证：超过上限后请求被拒绝"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        # 先占满窗口
        for _ in range(3):
            await limiter.check("test:exceed", max_requests=3, window_seconds=60)
        # 第 4 次应被拒绝
        allowed, remaining, retry = await limiter.check("test:exceed", max_requests=3, window_seconds=60)
        assert allowed is False
        assert remaining == 0

    async def test_different_identifiers_independent(self, redis_manager):
        """验证：不同 identifier 的限流互相独立"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        # user_a 占满
        for _ in range(3):
            await limiter.check("user_a", max_requests=3, window_seconds=60)
        # user_a 被拒绝
        allowed_a, _, _ = await limiter.check("user_a", max_requests=3, window_seconds=60)
        assert allowed_a is False
        # user_b 不受影响
        allowed_b, remaining_b, _ = await limiter.check("user_b", max_requests=3, window_seconds=60)
        assert allowed_b is True
        assert remaining_b == 2

    async def test_reset_clears_counter(self, redis_manager):
        """验证：reset 后计数归零"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        for _ in range(3):
            await limiter.check("test:reset", max_requests=3, window_seconds=60)
        # 先验证已满
        allowed, _, _ = await limiter.check("test:reset", max_requests=3, window_seconds=60)
        assert allowed is False
        # reset
        await limiter.reset("test:reset")
        # 重置后应放行
        allowed, remaining, _ = await limiter.check("test:reset", max_requests=3, window_seconds=60)
        assert allowed is True
        assert remaining == 2

    async def test_get_current_count(self, redis_manager):
        """验证：get_current_count 返回正确计数"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        assert await limiter.get_current_count("test:count") == 0
        for i in range(4):
            await limiter.check("test:count", max_requests=10, window_seconds=60)
            assert await limiter.get_current_count("test:count") == i + 1

    async def test_window_expires_eventually(self, redis_manager):
        """验证：短窗口过期后计数重置（使用 1 秒窗口）"""
        limiter = SlidingWindowRateLimiter(redis_manager)
        for _ in range(3):
            await limiter.check("test:expire", max_requests=3, window_seconds=1)
        # 窗口已满
        allowed, _, _ = await limiter.check("test:expire", max_requests=3, window_seconds=1)
        assert allowed is False
        # 等待窗口过期（稍多于 1 秒）
        await asyncio.sleep(1.2)
        # 过期后应放行
        allowed, remaining, _ = await limiter.check("test:expire", max_requests=3, window_seconds=1)
        assert allowed is True
        assert remaining == 2


# ====================================================================
# Test Suite 3: StateCache
# ====================================================================


class TestStateCache:
    """Agent 状态缓存测试"""

    @pytest.fixture
    def cache(self, redis_manager):
        """创建独立的 StateCache 实例"""
        return StateCache(redis_manager)

    @pytest.fixture(autouse=True)
    async def cleanup(self, redis_manager):
        """每个测试前后清理缓存 key"""
        yield
        keys = await redis_manager.client.keys("state:*")
        if keys:
            await redis_manager.client.delete(*keys)

    # ---- 基础 CRUD ----

    async def test_set_and_get(self, cache):
        """验证：基本写入和读取"""
        data = {"name": "test_session", "step": 5, "active": True}
        ok = await cache.set("session:crud_test", data, ttl=60)
        assert ok is True
        result = await cache.get("session:crud_test")
        assert result == data

    async def test_get_nonexistent_key(self, cache):
        """验证：读取不存在的 key 返回 None"""
        result = await cache.get("nonexistent_key")
        assert result is None

    async def test_delete(self, cache):
        """验证：删除后读取返回 None"""
        await cache.set("session:del_test", {"data": 1}, ttl=60)
        deleted = await cache.delete("session:del_test")
        assert deleted is True
        assert await cache.get("session:del_test") is None

    async def test_delete_nonexistent(self, cache):
        """验证：删除不存在的 key 返回 False"""
        deleted = await cache.delete("nonexistent")
        assert deleted is False

    async def test_exists(self, cache):
        """验证：exists 正确反映 key 存在性"""
        assert await cache.exists("session:exist_test") is False
        await cache.set("session:exist_test", {"x": 1}, ttl=60)
        assert await cache.exists("session:exist_test") is True

    # ---- 数据类型 ----

    async def test_complex_data_types(self, cache):
        """验证：复杂数据类型（嵌套、None、列表）正确序列化"""
        data = {
            "string": "hello",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "null_value": None,
            "nested": {"a": [1, 2, 3], "b": {"deep": "value"}},
            "list": [1, "two", 3.0, None],
        }
        await cache.set("session:types", data, ttl=60)
        result = await cache.get("session:types")
        assert result == data

    async def test_unicode_data(self, cache):
        """验证：中文字符正确序列化和反序列化"""
        data = {"任务描述": "打开百度搜索Python", "状态": "运行中", "步骤": 3}
        await cache.set("session:unicode", data, ttl=60)
        result = await cache.get("session:unicode")
        assert result == data

    # ---- TTL 过期 ----

    async def test_ttl_expiry(self, cache):
        """验证：短 TTL 过期后 key 自动删除"""
        await cache.set("session:ttl_test", {"data": 1}, ttl=1)
        assert await cache.exists("session:ttl_test") is True
        await asyncio.sleep(1.5)
        assert await cache.exists("session:ttl_test") is False
        assert await cache.get("session:ttl_test") is None

    async def test_get_ttl(self, cache):
        """验证：get_ttl 返回正确的剩余过期时间"""
        await cache.set("session:ttl_query", {"x": 1}, ttl=300)
        ttl = await cache.get_ttl("session:ttl_query")
        assert ttl > 0 and ttl <= 300  # 剩余时间应 <= 300

    async def test_get_ttl_nonexistent(self, cache):
        """验证：不存在的 key 返回 -2"""
        ttl = await cache.get_ttl("nonexistent_ttl_key")
        assert ttl == -2

    async def test_expire_update(self, cache):
        """验证：expire 方法更新 TTL"""
        await cache.set("session:expire_test", {"x": 1}, ttl=60)
        updated = await cache.expire("session:expire_test", 600)
        assert updated is True
        ttl = await cache.get_ttl("session:expire_test")
        assert ttl > 60  # 新 TTL 应该比原来的 60 大

    # ---- 批量操作 ----

    async def test_mset_and_mget(self, cache):
        """验证：批量写入和批量读取"""
        mapping = {
            "batch:a": {"id": 1, "name": "Alice"},
            "batch:b": {"id": 2, "name": "Bob"},
            "batch:c": {"id": 3, "name": "Charlie"},
        }
        ok = await cache.mset(mapping, ttl=60)
        assert ok is True

        results = await cache.mget(["batch:a", "batch:b", "batch:c", "batch:nonexist"])
        assert results["batch:a"] == {"id": 1, "name": "Alice"}
        assert results["batch:b"] == {"id": 2, "name": "Bob"}
        assert results["batch:c"] == {"id": 3, "name": "Charlie"}
        assert results["batch:nonexist"] is None

    async def test_mget_empty_list(self, cache):
        """验证：空列表批量读取返回空字典"""
        results = await cache.mget([])
        assert results == {}

    # ---- 边界情况 ----

    async def test_overwrite_existing_key(self, cache):
        """验证：对同一 key 重复写入会覆盖旧值"""
        await cache.set("session:overwrite", {"version": 1}, ttl=60)
        await cache.set("session:overwrite", {"version": 2}, ttl=60)
        result = await cache.get("session:overwrite")
        assert result == {"version": 2}

    async def test_zero_ttl_means_no_expiry(self, cache):
        """验证：TTL=0 表示永不过期（需要手动删除）"""
        await cache.set("session:no_expiry", {"data": 1}, ttl=0)
        await asyncio.sleep(1.0)
        assert await cache.exists("session:no_expiry") is True
        # 手动清理
        await cache.delete("session:no_expiry")
