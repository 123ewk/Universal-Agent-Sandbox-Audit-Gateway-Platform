"""
Browser Skills — 浏览器操作技能（MVP 8 核心 Skill 中的 5 个）

所有浏览器操作的抽象基类 + 具体实现。
当前为 Phase 4 接口定义 + 基础实现，Playwright 调用将在 Phase 5 接入。

风险等级：
  L1: goto, screenshot, extract_text（只读）
  L2: click, type（交互）
"""
from app.skills.base import BaseSkill, SkillCategory, SkillContext, SkillResult
from app.skills.enums import RiskLevel


# ====================================================================
# L1 — 只读操作
# ====================================================================


class GotoSkill(BaseSkill):
    """导航到指定 URL（只读，L1）"""
    name = "browser_goto"
    description = "导航到指定的 URL 地址"
    category = SkillCategory.BROWSER
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        url = params.get("url", "")
        if not url:
            return SkillResult.fail("缺少必要参数: url")
        # Phase 5 接入 Playwright 实际导航逻辑
        return SkillResult.ok(data={"url": url, "status": "navigation_scheduled"})


class ScreenshotSkill(BaseSkill):
    """截取当前页面截图（只读，L1）"""
    name = "browser_screenshot"
    description = "截取当前浏览器页面的截图"
    category = SkillCategory.BROWSER
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        # Phase 5 接入 Playwright 实际截屏逻辑
        return SkillResult.ok(data={"screenshot_path": None})


# ====================================================================
# L2 — 交互操作
# ====================================================================


class ClickSkill(BaseSkill):
    """点击页面元素（交互，L2）"""
    name = "browser_click"
    description = "点击页面上的元素，通过 CSS 选择器或坐标定位"
    category = SkillCategory.BROWSER
    risk_level = RiskLevel.L2_INTERACTION

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "")
        if not selector:
            return SkillResult.fail("缺少必要参数: selector")
        return SkillResult.ok(data={"selector": selector, "status": "click_scheduled"})


class TypeSkill(BaseSkill):
    """在页面元素中输入文本（交互，L2）"""
    name = "browser_type"
    description = "在指定的输入框中输入文本内容"
    category = SkillCategory.BROWSER
    risk_level = RiskLevel.L2_INTERACTION

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "")
        text = params.get("text", "")
        if not selector:
            return SkillResult.fail("缺少必要参数: selector")
        return SkillResult.ok(data={"selector": selector, "text_length": len(text)})


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
    risk_level = RiskLevel.L1_READONLY

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        selector = params.get("selector", "")  # 可选：CSS 选择器限定区域
        return SkillResult.ok(data={
            "selector": selector or "body",
            "text_length": 0,
            "content_preview": "",
        })
