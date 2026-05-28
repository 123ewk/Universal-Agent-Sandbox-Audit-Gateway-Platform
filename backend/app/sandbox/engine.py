"""
SandboxEngine — 浏览器操作统一封装

设计动机：
  Skill 不直接操作 Playwright Page，而是通过 SandboxEngine 的稳定 API。
  这样底层浏览器实现（Playwright/Puppeteer/Selenium）可以替换，
  不影响上层 Skill 和 Agent 逻辑。

核心职责：
  1. 封装 Page 操作（navigate/click/type/screenshot/extract_text）
  2. 注入安全拦截（URL 检查 + route 拦截）
  3. 提供 get_page_info() 返回清洗后的 PageInfo
  4. 每步截图（可配置）

Skill → SandboxEngine → Playwright Page → BrowserContext → Chromium
                    ↑                    ↑
              security.py         screenshots.py

使用方式：
  engine = SandboxEngine(
      provider=provider,
      session_id=42,
      security=SandboxSecurity(),
      screenshots=ScreenshotManager(),
  )
  await engine.create_context()
  await engine.navigate("https://www.baidu.com")
  page_info = await engine.get_page_info()
  await engine.click("#kw")
  await engine.cleanup()
"""
import logging
import time
from typing import Any, Optional

from app.sandbox.models import ActionResult, PageInfo
from app.sandbox.provider import SandboxProvider
from app.sandbox.security import SandboxSecurity, SandboxSecurityError
from app.sandbox.screenshot import ScreenshotManager
from app.config import settings

logger = logging.getLogger(__name__)


class SandboxEngineError(Exception):
    """SandboxEngine 相关错误"""
    pass


class SandboxEngine:
    """
    浏览器沙箱引擎

    每个 Agent Session 创建一个实例，持有独立的 BrowserContext 和 Page。
    """

    def __init__(
        self,
        provider: SandboxProvider,
        session_id: int,
        security: Optional[SandboxSecurity] = None,
        screenshots: Optional[ScreenshotManager] = None,
    ) -> None:
        self.provider = provider
        self.session_id = session_id
        self.security = security or SandboxSecurity()
        self.screenshots = screenshots or ScreenshotManager()

        self._context: Any = None
        self._page: Any = None
        self._page_info: Optional[PageInfo] = None
        self._step_number: int = 0

    # ================================================================
    # 属性
    # ================================================================

    @property
    def page(self) -> Any:
        """Playwright Page 实例（仅 SandboxEngine 内部和 test 使用）"""
        return self._page

    @property
    def page_url(self) -> str:
        return self._page.url if self._page else ""

    @property
    def page_title(self) -> str:
        """页面 title（同步获取缓存的 page_info）"""
        if self._page_info:
            return self._page_info.title
        return ""

    @property
    def step_number(self) -> int:
        return self._step_number

    # ================================================================
    # 生命周期
    # ================================================================

    async def create_context(self) -> None:
        """
        创建 BrowserContext 和 Page

        每次 Agent Session 开始时调用。
        自动注入安全拦截（route interception）。
        """
        self._context = await self.provider.create_context(
            session_id=self.session_id,
        )
        self._page = await self._context.new_page()

        # 注入安全拦截
        await self.security.setup_route_interception(self._page)

        logger.info(
            "SandboxEngine 已初始化: session_id=%d",
            self.session_id,
        )

    async def cleanup(self) -> None:
        """
        清理资源

        Session 结束时调用。
        先关闭 Page 再销毁 Context。
        """
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        await self.provider.destroy_context(self.session_id)
        self._context = None
        self._page_info = None
        logger.info("SandboxEngine 已清理: session_id=%d", self.session_id)

    # ================================================================
    # 核心操作
    # ================================================================

    async def navigate(
        self,
        url: str,
        timeout: int | None = None,
        wait_until: str = "domcontentloaded",
    ) -> ActionResult:
        """
        导航到指定 URL

        Args:
            url:        目标 URL
            timeout:    超时时间（秒），默认使用配置值
            wait_until: 等待策略 ("load" | "domcontentloaded" | "networkidle")

        Returns:
            ActionResult
        """
        if not self._page:
            return ActionResult.fail("浏览器未初始化，请先调用 create_context()")

        # 安全审查（第 1 层）
        try:
            self.security.check_url(url)
        except SandboxSecurityError as exc:
            return ActionResult.fail(str(exc))

        timeout = timeout or settings.SANDBOX_TIMEOUT_SECONDS
        start = time.monotonic()

        try:
            response = await self._page.goto(
                url,
                timeout=timeout * 1000,
                wait_until=wait_until,
            )
            elapsed = int((time.monotonic() - start) * 1000)

            status = response.status if response else None
            current_url = self._page.url
            title = await self._page.title()

            # 更新页面信息
            self._page_info = PageInfo(
                url=current_url,
                title=title,
                status_code=status,
            )

            logger.info(
                "导航完成: url=%s → %s, status=%s, time=%dms",
                url, current_url, status, elapsed,
            )

            return ActionResult.ok(
                data={
                    "url": current_url,
                    "title": title,
                    "status_code": status,
                },
                execution_time_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("导航失败: url=%s, error=%s, time=%dms", url, exc, elapsed)
            return ActionResult.fail(f"导航失败: {exc}")

    async def click(
        self,
        selector: str,
        timeout: int = 10,
    ) -> ActionResult:
        """
        点击元素

        Args:
            selector: CSS 选择器或文本选择器
            timeout:  等待元素出现的超时时间（秒）

        Returns:
            ActionResult
        """
        if not self._page:
            return ActionResult.fail("浏览器未初始化")

        # 高危行为检查
        check = self.security.check_action("click", selector=selector)
        if check.is_blocked:
            return ActionResult.fail(f"操作被安全策略拦截: {', '.join(check.reasons)}")
        if check.requires_approval:
            # 返回特殊标记，由 Agent 层的 AuditGateway 处理审批
            logger.warning("点击操作需要审批: selector=%s, reasons=%s",
                          selector, check.reasons)

        start = time.monotonic()
        try:
            await self._page.click(selector, timeout=timeout * 1000)
            elapsed = int((time.monotonic() - start) * 1000)

            return ActionResult.ok(
                data={
                    "selector": selector,
                    "status": "clicked",
                },
                execution_time_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return ActionResult.fail(f"点击失败: selector={selector}, error={exc}")

    async def type_text(
        self,
        selector: str,
        text: str,
        delay: int = 50,
        clear: bool = True,
    ) -> ActionResult:
        """
        在输入框中输入文本（模拟人类逐字输入）

        Args:
            selector: 输入框的 CSS 选择器
            text:     要输入的文本
            delay:    每个字符之间的延迟（毫秒）
            clear:    是否先清空已有内容

        Returns:
            ActionResult
        """
        if not self._page:
            return ActionResult.fail("浏览器未初始化")

        # 高危行为检查
        check = self.security.check_action("type", selector=selector, text=text)
        if check.is_blocked:
            return ActionResult.fail(f"操作被安全策略拦截: {', '.join(check.reasons)}")

        start = time.monotonic()
        try:
            if clear:
                await self._page.fill(selector, "")
            await self._page.type(selector, text, delay=delay)
            elapsed = int((time.monotonic() - start) * 1000)

            return ActionResult.ok(
                data={
                    "selector": selector,
                    "text_length": len(text),
                },
                execution_time_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return ActionResult.fail(f"输入失败: selector={selector}, error={exc}")

    async def screenshot(
        self,
        full_page: bool = True,
    ) -> ActionResult:
        """
        截取当前页面

        Args:
            full_page: 是否截取完整页面（含滚动区域）

        Returns:
            ActionResult，data 中包含截图路径
        """
        if not self._page:
            return ActionResult.fail("浏览器未初始化")

        self._step_number += 1
        result = await self.screenshots.capture(
            page=self._page,
            session_id=self.session_id,
            step_number=self._step_number,
            action="screenshot",
            full_page=full_page,
        )

        if result.path:
            # 更新缓存的 page_info
            if self._page_info:
                self._page_info.screenshot_path = result.path

            return ActionResult.ok(data=result.to_dict())
        else:
            return ActionResult.fail("截图保存失败")

    async def extract_text(
        self,
        selector: str = "body",
    ) -> ActionResult:
        """
        提取页面文本内容

        使用 Playwright page.evaluate() 获取 innerText，
        返回经过噪音过滤的文本。

        Args:
            selector: CSS 选择器，默认提取整个 body

        Returns:
            ActionResult，data 中包含文本内容和长度
        """
        if not self._page:
            return ActionResult.fail("浏览器未初始化")

        start = time.monotonic()
        try:
            # 提取指定元素的 innerText
            js_code = (
                f"document.querySelector('{selector}')"
                f"? document.querySelector('{selector}').innerText"
                f": ''"
            )
            text = await self._page.evaluate(js_code)
            elapsed = int((time.monotonic() - start) * 1000)

            # 基础清洗：合并多个空白行
            import re
            cleaned = re.sub(r'\n{3,}', '\n\n', text or "")

            return ActionResult.ok(
                data={
                    "selector": selector,
                    "text": cleaned,
                    "text_length": len(cleaned),
                },
                execution_time_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return ActionResult.fail(f"提取文本失败: selector={selector}, error={exc}")

    # ================================================================
    # 页面信息
    # ================================================================

    async def get_page_info(self) -> PageInfo:
        """
        获取当前页面的结构信息

        数据来源：
          - url/title：Playwright page 属性
          - cleaned_text：extract_text() 清洗后的内容
          - interactive_elements：page.evaluate() 提取交互元素
          - screenshot_path：最近截图路径

        核心铁律：不返回完整原始 HTML，只返回 cleaned_text + 交互元素清单。
        """
        if not self._page:
            return PageInfo()

        url = self._page.url
        title = await self._page.title()

        # 提取交互元素（不做完整 HTML 提取）
        elements = await self._extract_interactive_elements()

        # 提取清洗文本（前 500 字符预览）
        try:
            body_text = await self._page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            import re
            cleaned = re.sub(r'\n{3,}', '\n\n', body_text or "")[:500]
        except Exception:
            cleaned = ""

        self._page_info = PageInfo(
            url=url,
            title=title,
            cleaned_text=cleaned,
            interactive_elements=elements,
            screenshot_path=self._page_info.screenshot_path if self._page_info else None,
        )

        return self._page_info

    async def _extract_interactive_elements(self) -> list[dict[str, str]]:
        """
        提取当前页面可交互元素

        使用 JS 提取 button/input/a 的基本信息，
        避免返回完整 DOM。
        """
        if not self._page:
            return []

        try:
            elements = await self._page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('button, input, a, select, textarea').forEach(el => {
                        const tag = el.tagName.toLowerCase();
                        const text = (el.innerText || el.value || el.placeholder || '').trim().substring(0, 80);
                        const id = el.id || '';
                        const name = el.getAttribute('name') || '';
                        const type = el.type || '';
                        const href = el.href || '';
                        if (text || id || href) {
                            results.push({
                                tag: tag,
                                text: text,
                                id: id,
                                name: name,
                                type: type,
                                href: href.substring(0, 120),
                            });
                        }
                    });
                    return results.slice(0, 30);  // 最多 30 个元素
                }
            """)
            # 转换为统一格式
            formatted = []
            for el in (elements or []):
                formatted.append({
                    "type": f"{el.get('tag', '')}_{el.get('type', '')}".strip("_"),
                    "text": el.get("text", ""),
                    "selector": f"#{el['id']}" if el.get("id") else f"{el['tag']}[name='{el.get('name', '')}']" if el.get("name") else "",
                })
            return formatted
        except Exception as exc:
            logger.warning("提取交互元素失败: %s", exc)
            return []

    # ================================================================
    # 截图快捷方法（供 Agent _observe_node 使用）
    # ================================================================

    async def capture_step_screenshot(
        self,
        step_number: int,
        action: str,
    ) -> Optional[str]:
        """
        为当前步骤截图（Agent 执行每一步后自动调用）

        Args:
            step_number: 当前步骤号
            action:      操作名称

        Returns:
            截图文件路径，失败返回 None
        """
        self._step_number = step_number
        if not self._page:
            return None

        result = await self.screenshots.capture(
            page=self._page,
            session_id=self.session_id,
            step_number=step_number,
            action=action,
            full_page=True,
        )
        return result.path if result.path else None
