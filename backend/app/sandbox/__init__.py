"""
Sandbox 沙箱引擎 — Phase 6 核心模块

Skill → SandboxEngine → SandboxProvider → Playwright BrowserContext → Chromium

模块结构：
  models.py:         PageInfo / ActionResult 数据模型
  provider.py:       SandboxProvider 抽象接口
  local_provider.py: LocalPlaywrightProvider (本地 Chromium)
  engine.py:         SandboxEngine — navigate/click/type_text/screenshot/extract_text/get_page_info
  security.py:       双层防御 — URL 黑名单 + Playwright route 拦截 + 高危行为检测
  screenshot.py:     截图管理 — data/screenshots/{session_id}/step_{n}.png
"""
import logging
from typing import Optional

from app.sandbox.local_provider import LocalPlaywrightProvider
from app.sandbox.provider import SandboxProvider

logger = logging.getLogger(__name__)

_sandbox_provider: Optional[SandboxProvider] = None


def get_sandbox_provider() -> Optional[SandboxProvider]:
    """获取全局沙箱提供者单例（可能为 None，如果 Playwright 未安装）"""
    return _sandbox_provider


async def init_sandbox() -> SandboxProvider | None:
    """
    初始化沙箱提供者（在 FastAPI startup 中调用）

    尝试启动本地 Chromium，失败时返回 None（系统仍可运行，
    但浏览器技能将不可用）。
    """
    global _sandbox_provider
    try:
        provider = LocalPlaywrightProvider()
        await provider.launch()
        _sandbox_provider = provider
        logger.info("沙箱提供者已就绪: LocalPlaywrightProvider (sessions=%d)",
                     provider.get_session_count())
        return provider
    except Exception as exc:
        logger.warning("沙箱初始化失败（浏览器技能将不可用）: %s", exc)
        _sandbox_provider = None
        return None


async def shutdown_sandbox() -> None:
    """关闭沙箱提供者（在 FastAPI shutdown 中调用）"""
    global _sandbox_provider
    if _sandbox_provider:
        await _sandbox_provider.shutdown()
        _sandbox_provider = None
        logger.info("沙箱提供者已关闭")
