"""
Browser Skills — 浏览器操作技能（MVP 8 核心 Skill 中的 5 个）

所有浏览器操作的抽象基类 + 具体实现。
当前为 Phase 4 接口定义 + 基础实现，Playwright 调用将在 Phase 5 接入。

风险等级：
  L1: goto, screenshot, extract_text（只读）
  L2: click, type（交互）
"""
from app.skills.base import BaseSkill, SkillCategory, SkillContext, SkillResult
from app.skills.enums import RiskLevel, SkillTier


# ====================================================================
# L1 — 只读操作
# ====================================================================


class GotoSkill(BaseSkill):
    """导航到指定 URL（只读，L1）"""
    name = "browser_goto"
    description = "导航到指定的 URL 地址"
    category = SkillCategory.BROWSER
    tier = SkillTier.CORE
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        url = params.get("url", "")
        if not url:
            return SkillResult.fail("缺少必要参数: url")

        engine = context.sandbox_engine
        if engine is None:
            return SkillResult.ok(data={"url": url, "status": "navigation_scheduled"})

        result = await engine.navigate(url)
        return SkillResult(
            success=result.success, data=result.data,
            error=result.error, execution_time_ms=result.execution_time_ms,
        )


class ScreenshotSkill(BaseSkill):
    """截取当前页面截图（只读，L1）"""
    name = "browser_screenshot"
    description = "截取当前浏览器页面的截图"
    category = SkillCategory.BROWSER
    tier = SkillTier.CORE
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        engine = context.sandbox_engine
        if engine is None:
            return SkillResult.ok(data={"screenshot_path": None})

        result = await engine.screenshot()
        return SkillResult(
            success=result.success, data=result.data,
            error=result.error, execution_time_ms=result.execution_time_ms,
        )


# ====================================================================
# L2 — 交互操作
# ====================================================================


class ClickSkill(BaseSkill):
    """点击页面元素（交互，L2）"""
    name = "browser_click"
    description = "点击页面上的元素，通过 CSS 选择器或坐标定位"
    category = SkillCategory.BROWSER
    tier = SkillTier.INTERACTION
    risk_level = RiskLevel.L2_INTERACTION

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "")
        if not selector:
            return SkillResult.fail("缺少必要参数: selector")

        engine = context.sandbox_engine
        if engine is None:
            return SkillResult.ok(data={"selector": selector, "status": "click_scheduled"})

        result = await engine.click(selector)
        return SkillResult(
            success=result.success, data=result.data,
            error=result.error, execution_time_ms=result.execution_time_ms,
        )


class TypeSkill(BaseSkill):
    """在页面元素中输入文本（交互，L2）"""
    name = "browser_type"
    description = "在指定的输入框中输入文本内容"
    category = SkillCategory.BROWSER
    tier = SkillTier.INTERACTION
    risk_level = RiskLevel.L2_INTERACTION

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "")
        text = params.get("text", "")
        if not selector:
            return SkillResult.fail("缺少必要参数: selector")

        engine = context.sandbox_engine
        if engine is None:
            return SkillResult.ok(data={"selector": selector, "text_length": len(text)})

        result = await engine.type_text(selector, text)
        return SkillResult(
            success=result.success, data=result.data,
            error=result.error, execution_time_ms=result.execution_time_ms,
        )


class ExtractTextSkill(BaseSkill):
    """
    提取页面文本内容（只读，L1）

    区别于 screenshot（截图保存为图像），extract_text 通过 Playwright 的
    page.evaluate() 获取 document.body.innerText，返回纯文本，
    用于 LLM 理解页面内容或做后续的数据提取。
    预期输出是大段文本，LLM 消费前需要 Memory Compression 压缩摘要。
    """
    name = "browser_extract_text"
    description = "获取当前页面的所有可见文本内容"
    category = SkillCategory.BROWSER
    tier = SkillTier.CORE
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "body")

        engine = context.sandbox_engine
        if engine is None:
            return SkillResult.ok(data={
                "selector": selector,
                "text_length": 0,
                "content_preview": "",
            })

        result = await engine.extract_text(selector)
        return SkillResult(
            success=result.success, data=result.data,
            error=result.error, execution_time_ms=result.execution_time_ms,
        )
