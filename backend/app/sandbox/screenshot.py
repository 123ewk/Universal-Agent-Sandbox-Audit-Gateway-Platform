"""
ScreenshotManager — 截图持久化与路径管理

设计动机：
  每次 Agent 操作后自动截图，形成完整的操作证据链。
  截图存入本地文件系统，数据库只存路径引用（绝不存 BLOB）。

存储结构：
  data/screenshots/{session_id}/step_{n}_{action}.png

使用方式：
  mgr = ScreenshotManager(base_dir="data/screenshots")
  result = await mgr.capture(page, session_id=1, step=1, action="goto")
  # → ScreenshotResult(path="data/screenshots/1/step_01_goto.png", ...)
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ScreenshotResult:
    """单张截图的信息"""
    path: str = ""               # 文件系统路径
    filename: str = ""           # 文件名
    session_id: int = 0
    step_number: int = 0
    action: str = ""             # 操作名称（goto/click/type/screenshot）
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    captured_at: str = ""        # ISO 时间戳

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "session_id": self.session_id,
            "step_number": self.step_number,
            "action": self.action,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "captured_at": self.captured_at,
        }


class ScreenshotManager:
    """
    截图管理器

    职责：
      - 确保 session 目录存在
      - 按统一命名规则保存截图
      - 返回 ScreenshotResult（含路径、尺寸、时间戳）
    """

    def __init__(self, base_dir: str = "data/screenshots") -> None:
        self.base_dir = base_dir

    def get_session_dir(self, session_id: int) -> str:
        """获取指定 Session 的截图目录路径"""
        return os.path.join(self.base_dir, str(session_id))

    def ensure_session_dir(self, session_id: int) -> str:
        """确保 Session 截图目录存在，返回目录路径"""
        dir_path = self.get_session_dir(session_id)
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

    def build_filename(self, step_number: int, action: str) -> str:
        """构建截图文件名：step_{n}_{action}.png"""
        return f"step_{step_number:02d}_{action}.png"

    async def capture(
        self,
        page,
        session_id: int,
        step_number: int,
        action: str = "screenshot",
        full_page: bool = True,
    ) -> ScreenshotResult:
        """
        截取并保存页面截图

        Args:
            page:         Playwright Page 实例
            session_id:   Agent Session ID
            step_number:  当前步骤号
            action:       操作名称
            full_page:    是否截取完整页面（含滚动内容）

        Returns:
            ScreenshotResult
        """
        dir_path = self.ensure_session_dir(session_id)
        filename = self.build_filename(step_number, action)
        filepath = os.path.join(dir_path, filename)

        try:
            await page.screenshot(path=filepath, full_page=full_page)
            file_size = os.path.getsize(filepath)

            viewport = page.viewport_size or {}
            width = viewport.get("width", 0)
            height = viewport.get("height", 0)

            result = ScreenshotResult(
                path=filepath,
                filename=filename,
                session_id=session_id,
                step_number=step_number,
                action=action,
                size_bytes=file_size,
                width=width,
                height=height,
                captured_at=datetime.now(timezone.utc).isoformat(),
            )

            logger.info(
                "截图已保存: session=%d step=%d action=%s size=%d bytes",
                session_id, step_number, action, file_size,
            )
            return result

        except Exception as exc:
            logger.error(
                "截图失败: session=%d step=%d action=%s error=%s",
                session_id, step_number, action, exc,
            )
            return ScreenshotResult(
                path="",
                filename=filename,
                session_id=session_id,
                step_number=step_number,
                action=action,
            )

    def get_session_screenshots(self, session_id: int) -> list[str]:
        """获取指定 Session 的所有截图文件路径（按时间排序）"""
        dir_path = self.get_session_dir(session_id)
        if not os.path.isdir(dir_path):
            return []
        files = sorted(
            [os.path.join(dir_path, f) for f in os.listdir(dir_path) if f.endswith(".png")]
        )
        return files

    def cleanup_session(self, session_id: int) -> None:
        """清理指定 Session 的截图目录"""
        import shutil
        dir_path = self.get_session_dir(session_id)
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path)
            logger.info("截图目录已清理: session=%d", session_id)
