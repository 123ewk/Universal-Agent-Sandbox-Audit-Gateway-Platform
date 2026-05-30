"""
Agent API 路由 — FastAPI REST + WebSocket 端点

端点：
  POST /api/v1/tasks           创建并启动 Agent 任务（202 Accepted）
  GET  /api/v1/tasks/{id}      查询任务状态
  GET  /api/v1/tasks           列出所有任务
  WebSocket /ws/sessions/{id}  实时订阅执行事件流

架构（P0/P1 重构）：
  Router → TaskManager → AgentRuntime → EventBus → {WS, DB, Audit}
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMClient
from app.database import get_db_session
from app.engine.gateway import AuditGateway
from app.models.session import AgentSession as AgentSessionModel, SessionStatus
from app.schemas.common import APIResponse
from app.runtime.agent_runtime import AgentRuntime
from app.runtime.session_manager import get_session_manager
from app.runtime.task_manager import get_task_manager
from app.runtime.websocket_manager import get_websocket_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


# ====================================================================
# 请求/响应 Schema
# ====================================================================


class TaskCreateRequest(BaseModel):
    task_description: str = Field(
        ..., min_length=1, max_length=5000,
        description="自然语言任务描述",
        examples=["打开百度首页，搜索'天气'，截图保存"],
    )
    max_steps: int = Field(default=50, ge=1, le=200, description="最大执行步数")


class TaskResponse(BaseModel):
    task_id: int
    session_id: int
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: int
    session_id: int
    task_description: str
    status: str
    progress_pct: float
    current_step: int
    total_steps: int
    total_steps_executed: int
    llm_cost: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ====================================================================
# 全局 Runtime 实例（惰性初始化）
# ====================================================================

_runtime: Optional[AgentRuntime] = None


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        from app.sandbox import get_sandbox_provider
        _runtime = AgentRuntime(
            llm_client=LLMClient(),
            gateway=AuditGateway(),
            sandbox_provider=get_sandbox_provider(),
        )
    return _runtime


# ====================================================================
# REST 端点
# ====================================================================


@router.post("/tasks", response_model=APIResponse[TaskResponse], status_code=202)
async def create_task(
    req: TaskCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskResponse]:
    """
    创建并启动 Agent 任务

    流程：
      1. 创建 AgentSession 数据库记录
      2. TaskManager.start() 后台执行
      3. 立即返回 202
    """
    # 创建数据库记录
    session_model = AgentSessionModel(
        task_description=req.task_description,
        session_status=SessionStatus.PENDING,
        total_steps=req.max_steps,
        started_at=datetime.utcnow(),
    )
    db.add(session_model)
    await db.commit()
    await db.refresh(session_model)

    session_id = session_model.id

    # 初始化 SessionManager 状态
    sm = get_session_manager()
    sm.create(session_id, req.task_description, total_steps=req.max_steps)

    # 后台启动 Agent
    runtime = get_runtime()
    tm = get_task_manager()
    tm.start(
        session_id=session_id,
        coro=runtime.run(
            session_id=session_id,
            task_description=req.task_description,
            max_steps=req.max_steps,
        ),
        name=f"agent-{session_id}",
    )

    logger.info("[Router] 任务已创建: session_id=%d, task='%s'",
                session_id, req.task_description[:80])

    return APIResponse.success(data=TaskResponse(
        task_id=session_id,
        session_id=session_id,
        status=SessionStatus.PENDING.value,
        message="任务已提交，正在后台执行",
    ))


@router.get("/tasks/{task_id}", response_model=APIResponse[TaskStatusResponse])
async def get_task_status(
    task_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskStatusResponse]:
    """
    查询任务执行状态

    优先从 SessionManager（内存）获取实时状态，
    数据库作为持久化备份。
    """
    sm = get_session_manager()
    tm = get_task_manager()
    live = sm.get(task_id)

    # 从数据库获取基本信息
    session_model = await db.get(AgentSessionModel, task_id)
    if session_model is None and live is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if live:
        return APIResponse.success(data=TaskStatusResponse(
            task_id=task_id,
            session_id=task_id,
            task_description=live.task_description,
            status=live.status,
            progress_pct=live.progress_pct,
            current_step=live.total_steps_executed,
            total_steps=live.total_steps,
            total_steps_executed=live.total_steps_executed,
            llm_cost=live.llm_cost,
            error_message=live.error_message,
            created_at=live.created_at if live.created_at else None,
            updated_at=live.updated_at if live.updated_at else None,
        ))

    # 只从数据库获取
    return APIResponse.success(data=TaskStatusResponse(
        task_id=task_id,
        session_id=task_id,
        task_description=session_model.task_description,
        status=session_model.session_status.value,
        progress_pct=0.0,
        current_step=session_model.current_step,
        total_steps=session_model.total_steps,
        total_steps_executed=session_model.current_step,
        llm_cost=str(session_model.llm_cost),
        error_message=session_model.error_message,
        created_at=session_model.created_at.isoformat() if session_model.created_at else None,
        updated_at=session_model.updated_at.isoformat() if session_model.updated_at else None,
    ))


@router.get("/tasks", response_model=APIResponse[list[TaskStatusResponse]])
async def list_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[TaskStatusResponse]]:
    """列出最近的任务"""
    from sqlalchemy import select

    sm = get_session_manager()

    stmt = (
        select(AgentSessionModel)
        .order_by(AgentSessionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    tasks = []
    for s in sessions:
        live = sm.get(s.id)
        tasks.append(TaskStatusResponse(
            task_id=s.id,
            session_id=s.id,
            task_description=s.task_description,
            status=live.status if live else s.session_status.value,
            progress_pct=live.progress_pct if live else 0.0,
            current_step=live.total_steps_executed if live else s.current_step,
            total_steps=live.total_steps if live else s.total_steps,
            total_steps_executed=live.total_steps_executed if live else s.current_step,
            llm_cost=live.llm_cost if live else str(s.llm_cost),
            error_message=live.error_message if live else s.error_message,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=live.updated_at if live else (
                s.updated_at.isoformat() if s.updated_at else None
            ),
        ))
    return APIResponse.success(data=tasks)


# ====================================================================
# WebSocket 端点
# ====================================================================


@router.websocket("/ws/sessions/{session_id}")
async def websocket_session(
    websocket: WebSocket,
    session_id: int,
):
    """
    实时订阅 Agent Session 的执行事件流

    使用 WebSocketManager（封装 ConnectionManager），
    AgentRuntime 通过 EventBus → WebSocketManager 推送事件。
    """
    wsm = get_websocket_manager()

    await wsm.connect(websocket, session_id)

    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        logger.info("[WebSocket] 客户端断开: session_id=%d", session_id)
    except Exception as exc:
        logger.error("[WebSocket] 异常: session_id=%d, error=%s", session_id, exc)
    finally:
        await wsm.disconnect(websocket, session_id)
