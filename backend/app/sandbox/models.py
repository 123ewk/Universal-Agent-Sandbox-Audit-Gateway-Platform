"""
Sandbox 数据模型 — PageInfo / ActionResult

设计动机：
  SandboxEngine 与外部（Skills/Agent/ObservationPipeline）之间的数据交换
  使用统一的 dataclass 而非裸 dict，确保类型安全与 IDE 补全。

PageInfo：
  engine.get_page_info() 的返回值，已经过噪音过滤，
  不会包含完整原始 HTML（完整 HTML 不对外暴露）。

ActionResult：
  每个 SandboxEngine 方法的统一返回值格式，
  Skill 将其转换为 SkillResult。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PageInfo:
    """
    页面快照信息

    engine.get_page_info() 返回此结构，
    已去除 script/style/ad/tracker 等噪音内容。
    原始 HTML 不对外暴露，只保留 cleaned_text + interactive_elements。
    """
    url: str = ""
    title: str = ""
    cleaned_text: str = ""  # 已清洗的纯文本（去 script/style/ad）
    interactive_elements: list[dict[str, str]] = field(default_factory=list)
    screenshot_path: Optional[str] = None  # 最近一次截图路径
    status_code: Optional[int] = None      # HTTP 响应码

    @property
    def text_preview(self) -> str:
        """文本预览（前 200 字符）"""
        return self.cleaned_text[:200] if self.cleaned_text else ""

    @property
    def element_count(self) -> int:
        """可交互元素数量"""
        return len(self.interactive_elements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "cleaned_text_preview": self.text_preview,
            "interactive_elements": self.interactive_elements[:20],
            "screenshot_path": self.screenshot_path,
            "status_code": self.status_code,
        }


@dataclass
class ActionResult:
    """
    SandboxEngine 操作的统一返回值

    Skill 从中提取数据构造 SkillResult。
    """
    success: bool = True
    data: Any = None
    error: Optional[str] = None
    execution_time_ms: int = 0

    @classmethod
    def ok(cls, data: Any = None, execution_time_ms: int = 0) -> "ActionResult":
        return cls(success=True, data=data, execution_time_ms=execution_time_ms)

    @classmethod
    def fail(cls, error: str) -> "ActionResult":
        return cls(success=False, error=error)
