"""
ApprovalRecord 的 Pydantic Schema
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.models.approval import ApprovalStatus


class ApprovalCreate(BaseModel):
    """创建审批记录的请求体（Agent 触发高危操作时自动创建）"""
    session_id: int
    audit_log_id: Optional[int] = None
    risk_type: str = Field(..., max_length=64)
    risk_description: str = Field(..., min_length=1)
    risk_score: int = Field(default=50, ge=0, le=100)
    action_context: Optional[dict] = None
    expires_at: Optional[datetime] = None


class ApprovalResponse(BaseModel):
    """审批记录的 API 响应"""
    id: int
    session_id: int
    audit_log_id: Optional[int] = None
    risk_type: str
    risk_description: str
    risk_score: int
    action_context: Optional[dict] = None
    status: ApprovalStatus
    responded_at: Optional[datetime] = None
    response_note: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApprovalActionRequest(BaseModel):
    """人类操作员响应审批的请求体"""
    action: str = Field(..., pattern=r"^(approve|deny)$")
    note: Optional[str] = Field(None, max_length=500)
