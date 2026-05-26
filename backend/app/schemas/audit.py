"""
AuditLog 的 Pydantic Schema
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AuditLogCreate(BaseModel):
    """创建审计日志的请求体（通常是 Agent 内部调用，而非外部 API）"""
    session_id: int
    step_number: int = 0
    action_type: str = Field(..., max_length=64)
    action_input: Optional[dict] = None
    action_output: Optional[dict] = None
    is_high_risk: bool = False
    risk_reason: Optional[str] = None
    success: bool = True
    error_detail: Optional[str] = None
    execution_time_ms: int = 0
    action_taken_at: Optional[datetime] = None


class AuditLogResponse(BaseModel):
    """审计日志的 API 响应"""
    id: int
    session_id: int
    step_number: int
    action_type: str
    action_input: Optional[dict] = None
    action_output: Optional[dict] = None
    is_high_risk: bool
    risk_reason: Optional[str] = None
    approved: Optional[bool] = None
    success: bool
    error_detail: Optional[str] = None
    execution_time_ms: int
    action_taken_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
