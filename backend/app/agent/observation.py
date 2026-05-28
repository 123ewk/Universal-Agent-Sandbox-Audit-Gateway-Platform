"""
ObservationPipeline — 观察值处理流水线

设计动机：
  Agent 每步执行后从浏览器/环境获得的原始数据（HTML、DOM 树、日志）
  是极大且充满噪音的。直接注入 LLM 上下文会迅速耗尽 Token 预算。

  流水线将这些原始数据逐步压缩为结构化摘要：
    Browser DOM → Noise Filter → UI Parser → Summarizer → Structured Observation

  四个阶段：
    Stage 1 — Noise Filter:   删除 script/style/ad/tracking/hidden/cookie popup
    Stage 2 — UI Parser:      提取可交互元素（button/input/form/table/menu/title）
    Stage 3 — Summarizer:     生成一句话自然语言摘要
    Stage 4 — Structured Obs: 组装 ObservationRecord

核心铁律：
  原始数据不进 Prompt — 外部存储 → 摘要 + 引用路径。
  系统决定什么进上下文，不是 LLM 决定。

使用方式：
  pipeline = ObservationPipeline()
  observation = await pipeline.process(raw_html, page_url, page_title)
  state.last_observation = observation
"""
import hashlib
import logging
import re
from typing import Optional

from app.agent.state import ObservationRecord

logger = logging.getLogger(__name__)


class ObservationPipeline:
    """
    观察值处理流水线

    将原始 DOM/HTML 逐步压缩为结构化 ObservationRecord。
    """

    # ================================================================
    # Stage 1: Noise Filter — 删除无意义内容
    # ================================================================

    # 需要完整删除的标签（包括内容）
    REMOVE_TAGS = [
        "script", "style", "noscript", "iframe",
        "svg", "canvas", "video", "audio",
    ]

    # 常见 class/id 中包含这些关键词的元素（广告/追踪/cookie/隐藏）
    NOISE_CLASS_PATTERNS = [
        r"advertisement", r"ad-", r"adsbygoogle", r"banner-ad",
        r"tracking", r"analytics", r"pixel",
        r"cookie-banner", r"cookie-consent", r"cookie-notice",
        r"gdpr", r"ccpa",
        r"popup", r"modal-overlay", r"overlay",
        r"social-share", r"share-buttons",
        r"related-posts", r"recommended", r"you-may-also-like",
        r"sidebar-widget", r"footer-widget",
        r"newsletter-signup", r"subscribe",
        r"hidden", r"visually-hidden", r"sr-only",
        r"display:none", r"visibility:hidden",
    ]

    # ================================================================
    # Stage 2: UI Parser — 保留交互元素
    # ================================================================

    # 需要保留的交互元素标签
    INTERACTIVE_TAGS = {
        "button", "input", "select", "textarea", "form",
        "a", "table", "th", "td", "tr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "label", "title", "option",
        "nav", "menu", "menuitem",
        "header", "main",
    }

    # 提取表格结构
    TABLE_TAGS = {"table", "thead", "tbody", "tr", "th", "td"}

    def __init__(self, max_summary_length: int = 200) -> None:
        self.max_summary_length = max_summary_length

    # ================================================================
    # 主流程
    # ================================================================

    async def process(
        self,
        raw_html: str,
        page_url: str = "",
        page_title: str = "",
        raw_data_ref: Optional[str] = None,
    ) -> ObservationRecord:
        """
        处理原始 HTML，生成结构化观察记录

        Args:
            raw_html:     浏览器返回的原始 HTML
            page_url:     当前页面 URL
            page_title:   页面标题
            raw_data_ref: 原始数据的外部存储引用（文件路径/Redis key）

        Returns:
            结构化 ObservationRecord
        """
        # Stage 1: Noise Filter
        cleaned = self._noise_filter(raw_html)

        # Stage 2: UI Parser
        interactive_elements = self._ui_parser(cleaned)

        # Stage 3: Summarizer
        summary = self._summarizer(cleaned, page_title, interactive_elements)

        # Stage 4: 组装
        text_content = self._extract_text(cleaned)
        errors, warnings = self._detect_issues(cleaned, text_content)

        return ObservationRecord(
            summary=summary,
            page_title=page_title or self._extract_title(cleaned),
            page_url=page_url,
            interactive_elements=interactive_elements[:20],  # 最多 20 个元素
            errors=errors,
            warnings=warnings,
            raw_data_ref=raw_data_ref,
        )

    # ================================================================
    # Stage 1: Noise Filter
    # ================================================================

    def _noise_filter(self, html: str) -> str:
        """
        删除无意义标签和噪声元素

        保留：button, input, form, table, menu, title, heading, label
        删除：script, style, ad, tracking, hidden, cookie popup, iframe
        """
        if not html:
            return ""

        cleaned = html

        # 1. 删除完整标签（包括内容）
        for tag in self.REMOVE_TAGS:
            cleaned = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>",
                "",
                cleaned,
                flags=re.DOTALL | re.IGNORECASE,
            )
            # 自闭合形式
            cleaned = re.sub(
                rf"<{tag}[^>]*?/>",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )

        # 2. 删除 HTML 注释
        cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)

        # 3. 删除噪声 class/id 的元素
        for pattern in self.NOISE_CLASS_PATTERNS:
            cleaned = re.sub(
                rf'<[^>]*?(?:class|id)\s*=\s*["\'][^"\']*{pattern}[^"\']*["\'][^>]*>.*?</[^>]+>',
                "",
                cleaned,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # 4. 删除空白行和多余空格
        cleaned = re.sub(r"\n\s*\n", "\n", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)

        return cleaned.strip()

    # ================================================================
    # Stage 2: UI Parser
    # ================================================================

    def _ui_parser(self, html: str) -> list[dict[str, str]]:
        """
        提取可交互的 UI 元素

        返回格式：
          [{type: "button", text: "登录", selector: "#login-btn"}, ...]
        """
        elements: list[dict[str, str]] = []

        # 提取各种交互元素
        elements.extend(self._extract_buttons(html))
        elements.extend(self._extract_inputs(html))
        elements.extend(self._extract_links(html))
        elements.extend(self._extract_headings(html))

        return elements

    def _extract_buttons(self, html: str) -> list[dict[str, str]]:
        """提取按钮元素"""
        buttons: list[dict[str, str]] = []
        # <button ...>text</button>
        for match in re.finditer(
            r'<button[^>]*?>(.*?)</button>',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            btn_id = re.search(r'id\s*=\s*["\']([^"\']+)["\']', match.group(0))
            selector = f"#{btn_id.group(1)}" if btn_id else f"button:has-text('{text[:30]}')"
            if text:
                buttons.append({"type": "button", "text": text[:80], "selector": selector})
        return buttons

    def _extract_inputs(self, html: str) -> list[dict[str, str]]:
        """提取输入元素"""
        inputs: list[dict[str, str]] = []
        for match in re.finditer(
            r'<(input|textarea|select)[^>]*?>',
            html,
            re.IGNORECASE,
        ):
            tag_type = match.group(1).lower()
            attrs = match.group(0)

            inp_name = re.search(r'name\s*=\s*["\']([^"\']+)["\']', attrs)
            inp_id = re.search(r'id\s*=\s*["\']([^"\']+)["\']', attrs)
            inp_type = re.search(r'type\s*=\s*["\']([^"\']+)["\']', attrs)
            inp_placeholder = re.search(r'placeholder\s*=\s*["\']([^"\']+)["\']', attrs)

            label = inp_name.group(1) if inp_name else inp_id.group(1) if inp_id else tag_type
            input_type = inp_type.group(1) if inp_type else "text"
            placeholder = inp_placeholder.group(1) if inp_placeholder else ""

            selector = f"#{inp_id.group(1)}" if inp_id else f"{tag_type}[name='{label}']"

            inputs.append({
                "type": f"input_{input_type}",
                "text": placeholder or label,
                "selector": selector,
            })
        return inputs

    def _extract_links(self, html: str) -> list[dict[str, str]]:
        """提取导航链接"""
        links: list[dict[str, str]] = []
        for match in re.finditer(
            r'<a[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            href = match.group(1)
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if text and not href.startswith("javascript:") and not href.startswith("#"):
                links.append({"type": "link", "text": text[:80], "selector": f"a:has-text('{text[:30]}')"})
        return links[:10]  # 最多 10 个链接

    def _extract_headings(self, html: str) -> list[dict[str, str]]:
        """提取标题元素"""
        headings: list[dict[str, str]] = []
        for match in re.finditer(
            r'<h([1-3])[^>]*>(.*?)</h\1>',
            html,
            re.DOTALL | re.IGNORECASE,
        ):
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if text:
                headings.append({"type": f"h{match.group(1)}", "text": text[:120], "selector": ""})
        return headings

    # ================================================================
    # Stage 3: Summarizer
    # ================================================================

    def _summarizer(
        self,
        html: str,
        page_title: str,
        elements: list[dict[str, str]],
    ) -> str:
        """
        生成一句话自然语言摘要

        摘要格式：
          "页面 '{title}'，包含 N 个按钮、M 个输入框、K 个链接"
        """
        if not page_title and not html:
            return "（空页面）"

        title_part = f"'{page_title}'" if page_title else "当前页面"

        # 统计元素类型
        type_counts: dict[str, int] = {}
        for el in elements:
            el_type = el.get("type", "unknown")
            type_counts[el_type] = type_counts.get(el_type, 0) + 1

        parts: list[str] = []
        if type_counts.get("button"):
            parts.append(f"{type_counts['button']} 个按钮")
        if type_counts.get("link"):
            parts.append(f"{type_counts['link']} 个链接")
        inputs = sum(
            v for k, v in type_counts.items()
            if k.startswith("input_") or k in ("input", "select", "textarea")
        )
        if inputs:
            parts.append(f"{inputs} 个输入框")

        if parts:
            return f"页面 {title_part}，包含 {', '.join(parts)}"
        else:
            return f"页面 {title_part}（无可交互元素）"

    # ================================================================
    # Stage 4 辅助方法
    # ================================================================

    def _extract_text(self, html: str) -> str:
        """提取页面纯文本（用于错误检测）"""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:500]

    def _extract_title(self, html: str) -> str:
        """提取页面标题"""
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if match:
            return re.sub(r"<[^>]+>", "", match.group(1)).strip()
        return ""

    def _detect_issues(self, html: str, text: str) -> tuple[list[str], list[str]]:
        """
        检测页面错误和警告

        检测项：
          - 错误关键词（error, exception, 404, 500, 错误）
          - 访问限制（access denied, forbidden, captcha）
          - 超时提示
          - 空页面
        """
        errors: list[str] = []
        warnings: list[str] = []

        lower_html = html.lower()
        lower_text = text.lower()

        # 错误检测
        error_patterns = [
            ("error", "页面包含错误信息"),
            ("exception", "页面显示异常"),
            ("404", "页面不存在 (404)"),
            ("500", "服务器错误 (500)"),
            ("403", "访问被禁止 (403)"),
            ("access denied", "访问被拒绝"),
            ("forbidden", "访问受限"),
            ("captcha", "页面要求验证码，可能触发反爬"),
        ]
        for pattern, msg in error_patterns:
            if pattern in lower_html:
                errors.append(msg)

        # 警告检测
        warning_patterns = [
            ("timeout", "页面加载可能超时"),
            ("请稍后重试", "页面要求稍后重试"),
            ("rate limit", "可能触发限流"),
            ("javascript", "页面需要 JavaScript（可能是 SPA）"),
        ]
        for pattern, msg in warning_patterns:
            if pattern in lower_text or pattern in lower_html:
                warnings.append(msg)

        # 空页面检测
        if not html or len(html) < 50:
            warnings.append("页面内容极少，可能是空白页或加载失败")

        return errors[:3], warnings[:3]
