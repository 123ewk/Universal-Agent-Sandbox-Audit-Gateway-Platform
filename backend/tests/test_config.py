"""
测试 config.py 模块：验证配置加载和属性计算是否正确
运行方式：在 backend/ 目录下执行 pytest tests/test_config.py -v
"""
import sys
import os

# 确保 backend/ 在 sys.path 中，避免 import 路径问题
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings


class TestSettings:
    """配置加载基础测试"""

    def test_default_settings_create_instance(self) -> None:
        """验证 Settings 使用默认值正常实例化"""
        s = Settings()
        assert s.APP_NAME != ""
        assert s.DB_HOST == "127.0.0.1"
        assert s.DB_PORT == 5432
        assert s.REDIS_PORT == 6379

    def test_database_url_async_format(self) -> None:
        """验证 database_url 属性生成正确的 asyncpg URL"""
        s = Settings()
        url = s.database_url
        assert url.startswith("postgresql+asyncpg://")
        assert "postgres" in url
        assert "127.0.0.1" in url
        assert "5432" in url

    def test_redis_url_without_password(self) -> None:
        """验证无密码时 redis_url 属性格式正确"""
        s = Settings(REDIS_PASSWORD=None)
        url = s.redis_url
        assert url.startswith("redis://")
        assert "127.0.0.1:6379" in url
        assert "@" not in url  # 无密码时不应出现 @

    def test_redis_url_with_password(self) -> None:
        """验证有密码时 redis_url 属性包含认证信息"""
        s = Settings(REDIS_PASSWORD="secret123")
        url = s.redis_url
        assert url.startswith("redis://:secret123@")

    def test_env_override(self) -> None:
        """验证环境变量可以覆盖默认值"""
        # 模拟环境变量覆盖
        s = Settings(DB_HOST="10.0.0.1", DB_PORT=6432)
        assert s.DB_HOST == "10.0.0.1"
        assert s.DB_PORT == 6432

    def test_sandbox_settings_exist(self) -> None:
        """验证沙箱相关配置存在且合理"""
        s = Settings()
        assert s.SANDBOX_PROVIDER in ("local", "docker")
        assert s.SANDBOX_TIMEOUT_SECONDS > 0
        assert s.MAX_STEPS_PER_SESSION > 0

    def test_url_blocklist_contains_dangerous_schemes(self) -> None:
        """验证 URL 黑名单包含危险协议"""
        s = Settings()
        assert any("file://" in entry for entry in s.URL_BLOCKLIST)
        assert any("chrome://" in entry for entry in s.URL_BLOCKLIST)
