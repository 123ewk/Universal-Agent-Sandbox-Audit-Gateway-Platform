"""
SandboxSecurity — 沙箱安全规则引擎

双层防御：
  第 1 层 — URL 黑名单：在 engine.navigate() 调用前检查，直接拒绝
  第 2 层 — 网络拦截：通过 Playwright page.route() 阻止跟踪/广告/恶意域的资源加载

高危行为检测：
  download / upload / payment / submit → 标记为需要 Human Approval

使用方式：
  security = SandboxSecurity(blocklist=settings.URL_BLOCKLIST)
  security.check_url("https://bank.com/transfer")   # → 允许但标记高危
  security.check_url("file:///etc/passwd")           # → 抛出 SandboxSecurityError

  # 注入到 Playwright
  await security.setup_route_interception(page)
"""
import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class SandboxSecurityError(Exception):
    """安全策略拦截异常"""
    pass


@dataclass
class SecurityCheck:
    """
    安全审查结果

    一次 URL 或行为的审查结论。
    """
    allowed: bool = True
    risk_score: int = 0          # 0-100
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = False
    is_blocked: bool = False


class SandboxSecurity:
    """
    沙箱安全规则引擎

    在 SandboxEngine 执行操作前进行安全检查，
    拦截恶意 URL 和标记高危行为。
    """

    # 默认 URL 黑名单（fnmatch glob 模式）
    DEFAULT_URL_BLOCKLIST: list[str] = [
        "file://*",
        "chrome://*",
        "chrome-extension://*",
        "about:blank",
        "about:config",
        "view-source:*",
        "data:*",
        "javascript:*",
    ]

    # 高危域名模式（导航到此域名类别 → 标记高危）
    HIGH_RISK_DOMAIN_PATTERNS: list[str] = [
        "bank", "payment", "transfer", "transaction",
        "admin", "root", "internal",
        "login", "signin", "oauth",
    ]

    # Playwright route 拦截的域名类型
    BLOCKED_DOMAIN_CATEGORIES: dict[str, list[str]] = {
        "ads": [
            "doubleclick.net", "googleadservices.com", "googlesyndication.com",
            "adservice.google.com", "advertising", "adserver",
        ],
        "tracking": [
            "google-analytics.com", "googletagmanager.com",
            "facebook.com/tr", "analytics", "tracker", "pixel",
            "hotjar.com", "clarity.ms",
        ],
        "malicious": [
            "malware", "phishing", "scam",
        ],
    }

    # 高危交互行为关键词（submit/payment/download → Human Approval）
    HIGH_RISK_ACTIONS: dict[str, str] = {
        "download": "触发文件下载",
        "upload": "触发文件上传",
        "payment": "触发支付操作",
        "submit": "提交表单",
        "delete": "删除操作",
        "signup": "注册操作",
    }

    def __init__(
        self,
        url_blocklist: Optional[list[str]] = None,
        enable_route_interception: bool = True,
    ) -> None:
        self.url_blocklist = url_blocklist or self.DEFAULT_URL_BLOCKLIST
        self.enable_route_interception = enable_route_interception

    # ================================================================
    # 第 1 层：URL 检查
    # ================================================================

    def check_url(self, url: str) -> SecurityCheck:
        """
        检查 URL 是否允许访问

        Args:
            url: 目标 URL

        Returns:
            SecurityCheck 审查结果

        Raises:
            SandboxSecurityError: URL 被黑名单拦截时直接抛出
        """
        check = SecurityCheck()

        # 黑名单检查
        for pattern in self.url_blocklist:
            if fnmatch.fnmatch(url.lower(), pattern.lower()):
                raise SandboxSecurityError(
                    f"URL 被安全策略拦截: '{url}' 匹配黑名单模式 '{pattern}'"
                )

        # 高危域名检测
        for pattern in self.HIGH_RISK_DOMAIN_PATTERNS:
            if pattern in url.lower():
                check.risk_score += 30
                check.reasons.append(f"URL 包含高危关键词: {pattern}")

        # 风险评分
        if check.risk_score >= 60:
            check.is_blocked = True
            check.allowed = False
        elif check.risk_score >= 40:
            check.requires_approval = True

        return check

    # ================================================================
    # 第 2 层：Playwright Route 拦截
    # ================================================================

    async def setup_route_interception(self, page) -> None:
        """
        在 Playwright Page 上设置请求拦截

        拦截跟踪、广告、恶意域名的网络请求，
        在浏览器层面阻止资源加载（节省带宽 + 隐私保护）。

        Args:
            page: Playwright Page 实例
        """
        if not self.enable_route_interception:
            return

        # 收集所有需要拦截的域名
        blocked = []
        for category, domains in self.BLOCKED_DOMAIN_CATEGORIES.items():
            blocked.extend(domains)

        async def intercept(route) -> None:
            url = route.request.url.lower()
            for pattern in blocked:
                if pattern in url:
                    logger.debug("拦截请求: %s (匹配: %s)", route.request.url, pattern)
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", intercept)
        logger.info("Playwright route 拦截已启用，拦截规则: %d 条", len(blocked))

    # ================================================================
    # 高危行为检测
    # ================================================================

    def check_action(
        self,
        action_type: str,
        selector: str = "",
        text: str = "",
    ) -> SecurityCheck:
        """
        检查交互行为是否需要 Human Approval

        Args:
            action_type: 行为类型（click/type/download/upload/submit）
            selector:   目标元素的 CSS 选择器
            text:       输入文本（如果是 type 操作）

        Returns:
            SecurityCheck 结果
        """
        check = SecurityCheck()

        # 检测 selector 中的高危关键词
        action_lower = f"{selector} {text}".lower()
        for keyword, reason in self.HIGH_RISK_ACTIONS.items():
            if keyword in action_lower:
                check.risk_score += 25
                check.requires_approval = True
                check.reasons.append(f"高危行为: {reason} (关键词: {keyword})")

        # 行为类型本身的检查
        if action_type in ("download", "upload"):
            check.risk_score += 30
            check.requires_approval = True
            check.reasons.append(f"敏感操作类型: {action_type}")

        if check.risk_score >= 80:
            check.is_blocked = True
            check.allowed = False

        return check
