"""
LocalPlaywrightProvider — 本地 Chromium 沙箱提供者

设计动机：
  开发阶段使用本地安装的 Chromium，无需 Docker。
  生产阶段可切换到 DockerPlaywrightProvider（容器隔离），
  接口完全一致，业务代码无需修改。

架构：
  单 Browser 进程 + 多 BrowserContext（每 Session 一个 Context）
  - Browser 进程在 launch() 时启动，shutdown() 时关闭
  - 每个 Session 调用 create_context() 获取独立的 BrowserContext
  - Context 之间 cookie/storage/cache 完全隔离

使用方式：
  provider = LocalPlaywrightProvider(headless=False)
  await provider.launch()
  context = await provider.create_context(session_id=42)
  page = await context.new_page()
  ...
  await provider.destroy_context(session_id=42)
  await provider.shutdown()
"""
import logging
from typing import Any

from app.sandbox.provider import SandboxProvider, SandboxProviderError
from app.config import settings

logger = logging.getLogger(__name__)


class LocalPlaywrightProvider(SandboxProvider):
    """
    本地 Playwright Chromium 提供者

    管理一个 Browser 实例，为每个 Session 创建独立的 BrowserContext。
    所有 Playwright 对象通过此 Provider 管理，外部不直接操作 Playwright。
    """

    def __init__(
        self,
        headless: bool | None = None,
        viewport: dict[str, int] | None = None,
        locale: str = "zh-CN",
    ) -> None:
        super().__init__(
            headless=headless if headless is not None else settings.SANDBOX_HEADLESS,
            viewport=viewport or {"width": 1280, "height": 720},
        )
        self.locale = locale
        self._playwright = None
        self._browser = None
        self._contexts: dict[int, Any] = {}  # session_id → BrowserContext

    # ================================================================
    # 生命周期
    # ================================================================

    async def launch(self) -> None:
        """
        启动 Chromium 浏览器进程

        幂等：已启动时直接返回。
        """
        if self._is_launched:
            logger.info("浏览器已启动，跳过")
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise SandboxProviderError(
                "playwright 未安装。请运行: pip install playwright && playwright install chromium"
            )

        logger.info("正在启动本地 Chromium (headless=%s)...", self.headless)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",        # Docker 兼容
                "--disable-gpu",       # 无头模式关闭 GPU
                "--disable-dev-shm-usage",  # 避免 /dev/shm 不足
                "--disable-web-security",   # 跨域测试（开发阶段）
                "--disable-features=TranslateUI",  # 禁用翻译弹窗
            ],
        )
        self._is_launched = True
        logger.info("Chromium 已启动 (headless=%s)", self.headless)

    async def create_context(
        self,
        session_id: int,
        **kwargs: Any,
    ) -> Any:
        """
        为 Session 创建独立的 BrowserContext

        每个 Context 拥有独立的 cookie/storage/cache。
        重复调用会先销毁旧 Context 再创建新的。
        """
        if not self._is_launched or self._browser is None:
            raise SandboxProviderError("浏览器未启动，请先调用 launch()")

        # 如果 Session 已有 Context，先销毁
        if session_id in self._contexts:
            logger.warning("Session %d 已有 Context，先销毁旧的", session_id)
            await self.destroy_context(session_id)

        context = await self._browser.new_context(
            viewport=self.viewport,
            locale=self.locale,
            **kwargs,
        )
        self._contexts[session_id] = context
        logger.info(
            "BrowserContext 已创建: session_id=%d, viewport=%s, locale=%s, 活跃=%d",
            session_id, self.viewport, self.locale, len(self._contexts),
        )
        return context

    async def destroy_context(self, session_id: int) -> None:
        """销毁指定 Session 的 BrowserContext"""
        context = self._contexts.pop(session_id, None)
        if context is None:
            return
        try:
            await context.close()
            logger.info(
                "BrowserContext 已销毁: session_id=%d, 剩余活跃=%d",
                session_id, len(self._contexts),
            )
        except Exception as exc:
            logger.error("关闭 BrowserContext 失败: session_id=%d, error=%s",
                        session_id, exc)

    async def shutdown(self) -> None:
        """关闭浏览器，释放所有资源"""
        # 先关闭所有 Context
        for session_id in list(self._contexts.keys()):
            await self.destroy_context(session_id)

        # 关闭 Browser
        if self._browser:
            await self._browser.close()
            self._browser = None

        # 停止 Playwright
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._is_launched = False
        logger.info("Chromium 已关闭，所有资源已释放")

    # ================================================================
    # 查询方法
    # ================================================================

    def get_active_sessions(self) -> list[int]:
        return list(self._contexts.keys())

    def get_session_count(self) -> int:
        return len(self._contexts)

    def get_context(self, session_id: int) -> Any | None:
        """获取指定 Session 的 BrowserContext（内部使用）"""
        return self._contexts.get(session_id)
