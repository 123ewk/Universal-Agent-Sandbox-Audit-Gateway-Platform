"""
项目全局配置中心
使用 pydantic-settings 从 .env 文件和环境变量加载配置
所有配置项集中管理，严禁在其他模块中硬编码连接字符串或密钥
"""
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    全局配置类，继承自 pydantic-settings 的 BaseSettings
    自动从 .env 文件和环境变量中读取配置，优先级：环境变量 > .env > 默认值
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 变量名不区分大小写
        extra="ignore",        # 忽略未知的环境变量
    )

    # ==================== 应用基础 ====================
    APP_NAME: str = "Universal Agent Sandbox & Audit Gateway"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"  # 生产环境必须更换

    # ==================== PostgreSQL 数据库 ====================
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "agent_sandbox"
    DB_ECHO: bool = False  # 是否打印 SQL 日志（调试用）

    @property
    def database_url(self) -> str:
        """构建 PostgreSQL asyncpg 连接 URL"""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def sync_database_url(self) -> str:
        """构建 PostgreSQL 同步连接 URL（用于 Alembic 迁移）"""
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # ==================== Redis 连接 ====================
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None  # 当前为空密码
    REDIS_MAX_CONNECTIONS: int = 20  # 连接池最大连接数

    @property
    def redis_url(self) -> str:
        """构建 Redis 连接 URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ==================== LLM 大模型配置 ====================
    LLM_PROVIDER: Literal["openai", "deepseek", "claude"] = "deepseek"
    LLM_API_KEY: str = ""
    LLM_API_BASE: str = "https://api.deepseek.com/v1"
    LLM_MODEL_NAME: str = "deepseek-v4-flash"
    LLM_TEMPERATURE: float = 0.0  # Agent 执行需要确定性，设为 0
    LLM_MAX_TOKENS: int = 4096

    # ==================== Playwright 沙箱配置 ====================
    SANDBOX_PROVIDER: Literal["local", "docker"] = "local"
    SANDBOX_HEADLESS: bool = False         # 开发阶段设为 False 可观察浏览器操作
    SANDBOX_TIMEOUT_SECONDS: int = 30      # 单次浏览器操作超时时间
    SANDBOX_MAX_TABS: int = 5              # 最大并发浏览器上下文数
    SANDBOX_USER_DATA_DIR: str = "./data/browser_profiles"  # 浏览器用户数据目录

    # ==================== 安全策略 ====================
    URL_BLOCKLIST: list[str] = [
        "file://*",           # 禁止访问本地文件系统
        "chrome://*",         # 禁止访问 Chrome 内部页面
        "about:blank",        # 禁止空白页（需要明确目标）
    ]
    URL_ALLOWLIST: list[str] = []  # 空列表 = 不限制（生产环境应配置）
    HIGH_RISK_DOMAINS: list[str] = [
        "bank", "payment", "transfer",  # 金融类关键词
        "admin", "root",                # 管理员后台
    ]
    MAX_STEPS_PER_SESSION: int = 50  # 单个 Agent Session 最多执行步数

    # ==================== WebSocket 配置 ====================
    WS_HEARTBEAT_INTERVAL: int = 30   # 心跳间隔（秒）
    WS_MAX_CONNECTIONS_PER_USER: int = 5

    # ==================== 审计日志配置 ====================
    AUDIT_LOG_LEVEL: Literal["all", "sensitive_only"] = "all"
    AUDIT_RETENTION_DAYS: int = 90    # 审计日志保留天数


# ==================== 全局单例 ====================
# 整个项目中只实例化一次，其他模块通过 import 使用
# 例如：from app.config import settings
settings = Settings()
