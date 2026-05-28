"""
Screenshot Serving Router — 截图文件服务

端点：
  GET /api/screenshots?session_id={id}            — 列出 Session 的所有截图
  GET /api/screenshots/{filename}?session_id={id}  — 获取单个截图文件

设计动机：
  WS 只推送截图路径和文件名，前端通过 HTTP 单独加载图片。
  这避免了 WebSocket 传输二进制带来的帧拥塞问题。
"""
import logging
import os
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screenshots", tags=["Screenshots"])

SCREENSHOTS_BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "screenshots",
)


@router.get("/")
async def list_screenshots(
    session_id: int = Query(..., description="Session ID"),
):
    """
    列出指定 Session 的所有截图文件

    返回按步骤号排序的截图列表，每个截图包含：
      filename, step_number, action, size_bytes

    前端用于 Replay 功能：根据截图列表重建步骤时间线。
    """
    dir_path = os.path.join(SCREENSHOTS_BASE_DIR, str(session_id))
    if not os.path.isdir(dir_path):
        return JSONResponse({"session_id": session_id, "screenshots": [], "count": 0})

    files = sorted(
        [f for f in os.listdir(dir_path) if f.endswith(".png")]
    )

    screenshots = []
    for f in files:
        filepath = os.path.join(dir_path, f)
        size = os.path.getsize(filepath)

        # 从文件名解析: step_{n}_{action}.png
        name_no_ext = f.replace(".png", "")
        parts = name_no_ext.split("_", 1)  # ["step", "01_goto"]
        step_str = parts[1].split("_")[0] if len(parts) > 1 else "0"
        action = parts[1].split("_", 1)[1] if len(parts) > 1 and "_" in parts[1] else ""

        try:
            step_number = int(step_str)
        except ValueError:
            step_number = 0

        screenshots.append({
            "filename": f,
            "step_number": step_number,
            "action": action,
            "size_bytes": size,
        })

    return JSONResponse({
        "session_id": session_id,
        "screenshots": screenshots,
        "count": len(screenshots),
    })


@router.get("/{filename}")
async def get_screenshot(
    filename: str,
    session_id: int = Query(..., description="Session ID"),
):
    """
    获取指定 Session 的截图文件

    Args:
        filename:    截图文件名（如 step_01_goto.png）
        session_id:  Agent Session ID

    Returns:
        PNG 图片文件
    """
    # 路径遍历防护
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    file_path = os.path.join(SCREENSHOTS_BASE_DIR, str(session_id), filename)

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"截图不存在: {filename}")

    return FileResponse(
        file_path,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},  # 截图实时变化，不缓存
    )
