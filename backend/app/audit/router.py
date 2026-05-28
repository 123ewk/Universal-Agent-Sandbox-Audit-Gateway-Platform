"""
Audit API 路由 — 审批 REST 端点

端点：
  GET  /api/v1/approvals/pending       列出待审批请求
  GET  /api/v1/approvals/pending/{id}  查看单个审批请求详情
  POST /api/v1/approvals/{id}/approve  批准
  POST /api/v1/approvals/{id}/deny     拒绝
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit.approval import ApprovalManager, ApprovalStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/approvals", tags=["Approval"])

# 全局审批管理器实例（由 AgentGraph 注入）
_approval_manager: Optional[ApprovalManager] = None


def set_approval_manager(mgr: ApprovalManager) -> None:
    """设置全局审批管理器（应用启动时调用）"""
    global _approval_manager
    _approval_manager = mgr


def get_approval_manager() -> ApprovalManager:
    """获取全局审批管理器"""
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = ApprovalManager()
    return _approval_manager


# ====================================================================
# 响应 Schema
# ====================================================================


class ApprovalItem(BaseModel):
    """审批请求列表项"""
    id: int
    session_id: int
    skill_name: str
    step_number: int
    risk_score: int
    risk_reasons: list[str]
    status: str
    created_at: str
    expires_at: Optional[str] = None


class DenyRequest(BaseModel):
    """拒绝请求"""
    reason: str = Field(default="", description="拒绝原因")


class ApprovalActionResponse(BaseModel):
    """审批操作响应"""
    approval_id: int
    status: str
    message: str


# ====================================================================
# 端点
# ====================================================================


@router.get("/pending")
async def list_pending(
    session_id: Optional[int] = Query(default=None, description="按 Session 过滤"),
):
    """列出所有待审批的请求"""
    mgr = get_approval_manager()
    pending = mgr.get_pending(session_id=session_id)
    return {
        "count": len(pending),
        "items": [
            ApprovalItem(
                id=r.id,
                session_id=r.session_id,
                skill_name=r.skill_name,
                step_number=r.step_number,
                risk_score=r.risk_score,
                risk_reasons=r.risk_reasons,
                status=r.status.value,
                created_at=r.created_at.isoformat(),
                expires_at=r.expires_at.isoformat() if r.expires_at else None,
            )
            for r in pending
        ],
    }


@router.get("/pending/{approval_id}")
async def get_approval_detail(approval_id: int):
    """查看单个审批请求详情"""
    mgr = get_approval_manager()
    req = mgr.get_request(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"审批请求不存在: {approval_id}")
    return ApprovalItem(
        id=req.id,
        session_id=req.session_id,
        skill_name=req.skill_name,
        step_number=req.step_number,
        risk_score=req.risk_score,
        risk_reasons=req.risk_reasons,
        status=req.status.value,
        created_at=req.created_at.isoformat(),
        expires_at=req.expires_at.isoformat() if req.expires_at else None,
    )


@router.post("/{approval_id}/approve")
async def approve(approval_id: int) -> ApprovalActionResponse:
    """批准审批请求"""
    mgr = get_approval_manager()
    try:
        req = await mgr.approve(approval_id, resolved_by="api_user")
        return ApprovalActionResponse(
            approval_id=approval_id,
            status="approved",
            message=f"Skill '{req.skill_name}' 已批准执行",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{approval_id}/deny")
async def deny(approval_id: int, body: DenyRequest = DenyRequest()) -> ApprovalActionResponse:
    """拒绝审批请求"""
    mgr = get_approval_manager()
    try:
        req = await mgr.deny(approval_id, reason=body.reason, resolved_by="api_user")
        return ApprovalActionResponse(
            approval_id=approval_id,
            status="denied",
            message=f"Skill '{req.skill_name}' 已被拒绝",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
