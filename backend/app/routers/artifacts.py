"""
Artifact REST API — Agent 执行产物查询与下载

端点：
  GET  /api/v1/artifacts?session_id=X&type=Y  列出产物（支持筛选）
  GET  /api/v1/artifacts/{id}                   单个产物详情
  GET  /api/v1/artifacts/{id}/download          下载文件
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.artifact import Artifact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/artifacts", tags=["Artifact"])


# ====================================================================
# Response Schemas
# ====================================================================


class ArtifactItem(BaseModel):
    """产物列表项"""
    id: int
    session_id: int
    step_number: Optional[int] = None
    artifact_type: str
    mime_type: str
    filename: str
    size_bytes: int = 0
    created_at: Optional[str] = None


class ArtifactDetail(ArtifactItem):
    """产物详情（含 text_content 和 data_json）"""
    text_content: Optional[str] = None
    data_json: Optional[dict] = None
    file_path: Optional[str] = None


# ====================================================================
# Endpoints
# ====================================================================


@router.get("")
async def list_artifacts(
    session_id: Optional[int] = Query(default=None, description="按 Session 过滤"),
    artifact_type: Optional[str] = Query(default=None, description="产物类型: screenshot / agent_thought / tool_result / log"),
    step_number: Optional[int] = Query(default=None, description="按步骤号过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
):
    """列出产物，支持多条件过滤"""
    stmt = select(Artifact).order_by(Artifact.created_at.desc())

    # 参数过滤
    pending_filters = []
    if session_id is not None:
        pending_filters.append(Artifact.session_id == session_id)
    if artifact_type is not None:
        pending_filters.append(Artifact.artifact_type == artifact_type)
    if step_number is not None:
        pending_filters.append(Artifact.step_number == step_number)

    for f in pending_filters:
        stmt = stmt.where(f)

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    artifacts = result.scalars().all()

    return {
        "count": len(artifacts),
        "items": [
            ArtifactItem(
                id=a.id,
                session_id=a.session_id,
                step_number=a.step_number,
                artifact_type=a.artifact_type,
                mime_type=a.mime_type,
                filename=a.filename,
                size_bytes=a.size_bytes,
                created_at=a.created_at.isoformat() if a.created_at else None,
            )
            for a in artifacts
        ],
    }


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """获取单个产物详情"""
    result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产物不存在: {artifact_id}")

    return ArtifactDetail(
        id=artifact.id,
        session_id=artifact.session_id,
        step_number=artifact.step_number,
        artifact_type=artifact.artifact_type,
        mime_type=artifact.mime_type,
        filename=artifact.filename,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at.isoformat() if artifact.created_at else None,
        text_content=artifact.text_content,
        data_json=artifact.data_json,
        file_path=artifact.file_path,
    )


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """下载产物文件"""
    result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产物不存在: {artifact_id}")
    if not artifact.file_path:
        raise HTTPException(status_code=404, detail="该产物无可下载文件")

    import os
    full_path = artifact.file_path
    if not os.path.isabs(full_path):
        full_path = os.path.join("data", full_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=full_path,
        media_type=artifact.mime_type,
        filename=artifact.filename or f"artifact_{artifact_id}",
    )
