"""
AgentSession 的 Pydantic Schema

定义 API 与前端交换的 Session 数据结构。
ORM 模型（models/）负责数据库映射，Schema（schemas/）负责 API 序列化。
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field

from app.models.session import SessionStatus


# ==================== 创建请求 ====================
class SessionCreate(BaseModel):
    """创建 Session 的请求体"""
    task_description: str = Field(
        ..., min_length=1, max_length=10000,
        description="用户输入的自然语言任务描述",
    )
    session_tag: Optional[str] = Field(
        None, max_length=64,
        description="会话标签（可选）",
    )


# ==================== 响应模型 ====================
class SessionResponse(BaseModel):
    """Session 的 API 响应"""
    id: int
    task_description: str
    session_status: SessionStatus
    current_step: int
    total_steps: int
    llm_cost: Decimal
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    session_tag: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}  # 允许从 ORM 模型转换


class SessionListResponse(BaseModel):
    """Session 列表的响应"""
    items: list[SessionResponse]
    total: int
    page: int
    page_size: int
