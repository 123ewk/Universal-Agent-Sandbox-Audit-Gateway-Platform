"""
SandboxProvider — 沙箱提供者抽象接口

设计动机：
  当前开发阶段使用本地 Chromium，生产阶段需要 Docker 容器隔离或远程浏览器。
  SandboxProvider 定义统一的抽象接口，通过依赖注入切换实现，
  业务层（Agent/Skill）完全不感知底层浏览器的运行方式。

隔离粒度：BrowserContext（每 Session 独立）
  选择 BrowserContext 而非 Browser 或 Page 的理由：
  - BrowserContext 提供完整的 cookie/storage/cache 隔离
  - 创建/销毁速度快（<100ms）
  - 内存开销远小于独立 Browser 进程

后期可切换实现：
  LocalPlaywrightProvider → DockerPlaywrightProvider → RemoteBrowserProvider

使用方式：
  provider = LocalPlaywrightProvider(headless=True)
  await provider.launch()
  context = await provider.create_context(session_id=42)
  ...
  await provider.destroy_context(session_id=42)
  await provider.shutdown()
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SandboxProviderError(Exception):
    """SandboxProvider 相关错误"""
    pass


class SandboxProvider(ABC):
    """
    沙箱提供者抽象基类

    生命周期：
      launch() → create_context()* → destroy_context()* → shutdown()

    每个 Agent Session 调用一次 create_context()，
    获取独立的 BrowserContext 实例。
    """

    def __init__(self, headless: bool = True, viewport: dict[str, int] | None = None) -> None:
        self.headless = headless
        self.viewport = viewport or {"width": 1280, "height": 720}
        self._is_launched = False

    @property
    def is_launched(self) -> bool:
        return self._is_launched

    # ================================================================
    # 生命周期方法
    # ================================================================

    @abstractmethod
    async def launch(self) -> None:
        """
        启动浏览器进程

        在应用启动时调用一次（或首次请求时惰性初始化）。
        重复调用应安全（幂等）。
        """
        ...

    @abstractmethod
    async def create_context(self, session_id: int, **kwargs: Any) -> Any:
        """
        为指定 Session 创建独立的 BrowserContext

        Args:
            session_id: Agent Session ID
            **kwargs:   传递给 browser.new_context() 的额外参数

        Returns:
            BrowserContext 实例（Playwright 对象）
        """
        ...

    @abstractmethod
    async def destroy_context(self, session_id: int) -> None:
        """
        销毁指定 Session 的 BrowserContext

        Session 结束时调用，释放资源。
        如果 session_id 不存在应安全返回（不抛异常）。
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """
        关闭整个浏览器进程

        应用关闭时调用，释放所有资源。
        """
        ...

    # ================================================================
    # 工具方法
    # ================================================================

    @abstractmethod
    def get_active_sessions(self) -> list[int]:
        """返回当前活跃的 Session ID 列表"""
        ...

    @abstractmethod
    def get_session_count(self) -> int:
        """返回当前活跃的 Session 数量"""
        ...

    # ================================================================
    # 持久化方法（Phase 10: Browser Persistence）
    # ================================================================

    async def persist_context(self, session_id: int) -> bool:
        """
        持久化 Session 的浏览器上下文（cookies/storage）

        默认实现不持久化，子类按需 override。

        Returns:
            True 如果成功持久化，False 如果不支持
        """
        return False

    async def restore_context(self, session_id: int) -> Optional[Any]:
        """
        恢复 Session 的浏览器上下文

        默认实现不恢复，子类按需 override。

        Returns:
            BrowserContext 实例或 None
        """
        return None
