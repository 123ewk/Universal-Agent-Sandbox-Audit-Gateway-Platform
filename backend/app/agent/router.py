"""
Agent API 路由 — FastAPI REST + WebSocket 端点

设计动机：
  Agent 任务是长时间运行的（秒～分钟级），不能阻塞 HTTP 请求线程。
  采用 "提交任务 → 返回 task_id → 后台执行 → WebSocket 推送" 模式。

端点：
  POST /api/v1/tasks           创建并启动 Agent 任务（202 Accepted）
  GET  /api/v1/tasks/{id}      查询任务状态
  GET  /api/v1/tasks           列出所有任务
  WebSocket /ws/sessions/{id}  实时订阅执行事件流

使用方式：
  from app.agent.router import router
  app.include_router(router, prefix="/api/v1")
"""
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentGraph
from app.agent.llm import LLMClient
from app.agent.state import AgentState, AgentStatus
from app.database import get_db_session
from app.engine.gateway import AuditGateway
from app.models.session import AgentSession as AgentSessionModel, SessionStatus
from app.schemas.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


# ====================================================================
# 请求/响应 Schema
# ====================================================================


class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    task_description: str = Field(
        ..., min_length=1, max_length=5000,
        description="自然语言任务描述",
        examples=["打开百度首页，搜索'天气'，截图保存"],
    )
    max_steps: int = Field(default=50, ge=1, le=200, description="最大执行步数")


class TaskResponse(BaseModel):
    """任务创建响应"""
    task_id: int
    session_id: int
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
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
# 全局 AgentGraph 实例（惰性初始化）
# ====================================================================

_agent_graph: Optional[AgentGraph] = None
_active_tasks: dict[int, AgentState] = {}  # task_id → AgentState


def get_agent_graph() -> AgentGraph:
    """获取 AgentGraph 单例"""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = AgentGraph(
            llm_client=LLMClient(),
            gateway=AuditGateway(),
        )
    return _agent_graph


# ====================================================================
# REST 端点
# ====================================================================


@router.post("/tasks", response_model=APIResponse[TaskResponse], status_code=202)
async def create_task(
    req: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
) -> APIResponse[TaskResponse]:
    """
    创建并启动 Agent 任务

    1. 校验输入
    2. 创建 AgentSession 数据库记录
    3. 通过 BackgroundTasks 启动后台执行
    4. 立即返回 task_id（202 Accepted）
    """
    # 创建数据库记录
    session_model = AgentSessionModel(
        task_description=req.task_description,
        session_status=SessionStatus.PENDING,
        total_steps=req.max_steps,
        started_at=datetime.now(timezone.utc),
    )
    db.add(session_model)
    await db.commit()
    await db.refresh(session_model)

    session_id = session_model.id

    # 注册后台任务
    background_tasks.add_task(
        _run_agent_task,
        session_id=session_id,
        task_description=req.task_description,
        max_steps=req.max_steps,
    )

    logger.info("[Router] 任务已创建: session_id=%d, task='%s'",
                session_id, req.task_description[:80])

    return APIResponse.ok(TaskResponse(
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

    返回执行进度、当前步骤、费用等信息。
    """
    # 从数据库获取
    session_model = await db.get(AgentSessionModel, task_id)
    if session_model is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    # 从内存获取实时状态（如果有）
    live_state = _active_tasks.get(task_id)
    if live_state:
        progress = live_state.progress_pct
        current_step = live_state.current_step_index
        total_steps = len(live_state.plan_steps)
        total_executed = live_state.total_steps_executed
        cost = str(live_state.total_llm_cost)
        error = live_state.error_message
        status = live_state.agent_status.value
    else:
        progress = 0.0
        current_step = session_model.current_step
        total_steps = session_model.total_steps
        total_executed = session_model.current_step
        cost = str(session_model.llm_cost)
        error = session_model.error_message
        status = session_model.session_status.value

    return APIResponse.ok(TaskStatusResponse(
        task_id=task_id,
        session_id=task_id,
        task_description=session_model.task_description,
        status=status,
        progress_pct=round(progress, 1),
        current_step=current_step,
        total_steps=total_steps,
        total_steps_executed=total_executed,
        llm_cost=cost,
        error_message=error,
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

    stmt = (
        select(AgentSessionModel)
        .order_by(AgentSessionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    tasks = [
        TaskStatusResponse(
            task_id=s.id,
            session_id=s.id,
            task_description=s.task_description,
            status=s.session_status.value,
            progress_pct=0.0,
            current_step=s.current_step,
            total_steps=s.total_steps,
            total_steps_executed=s.current_step,
            llm_cost=str(s.llm_cost),
            error_message=s.error_message,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
        )
        for s in sessions
    ]
    return APIResponse.ok(tasks)


# ====================================================================
# WebSocket 端点
# ====================================================================


@router.websocket("/ws/sessions/{session_id}")
async def websocket_session(
    websocket: WebSocket,
    session_id: int,
):
    """
    实时订阅 Agent Session 的执行事件流（Phase 7 升级版）

    基于 ConnectionManager 的事件推送架构，
    Agent 通过 ws_manager.broadcast() 推送事件，
    前端通过此端点订阅。

    事件命名空间：agent.* / sandbox.* / audit.* / approval.* / system.*
    """
    from app.ws.manager import ConnectionManager
    from app.ws.protocol import EventType

    # 获取全局 ConnectionManager
    manager = _get_ws_manager()

    await manager.connect(websocket, session_id)

    try:
        # 保持连接存活，事件由 AgentGraph 通过 manager.broadcast() 推送
        while True:
            await asyncio.sleep(30)  # 心跳已在 manager 层处理
    except WebSocketDisconnect:
        logger.info("[WebSocket] 客户端断开: session_id=%d", session_id)
    except Exception as exc:
        logger.error("[WebSocket] 异常: session_id=%d, error=%s", session_id, exc)
    finally:
        await manager.disconnect(websocket, session_id)


# ====================================================================
# 全局 WS Manager（惰性初始化）
# ====================================================================

_ws_manager: Optional[Any] = None


def _get_ws_manager():
    global _ws_manager
    if _ws_manager is None:
        from app.ws.manager import ConnectionManager
        _ws_manager = ConnectionManager()
    return _ws_manager


def set_ws_manager(mgr: Any) -> None:
    global _ws_manager
    _ws_manager = mgr


# ====================================================================
# 后台任务执行
# ====================================================================


async def _run_agent_task(
    session_id: int,
    task_description: str,
    max_steps: int = 50,
) -> None:
    """
    后台异步执行 Agent 任务

    1. 创建 AgentGraph 实例
    2. 调用 graph.invoke() 执行
    3. 更新数据库记录
    4. 存活跃状态供 WebSocket 查询
    """
    from app.database import _SessionFactory

    graph = get_agent_graph()

    try:
        logger.info("[Background] 开始执行任务: session_id=%d", session_id)
        state = await graph.invoke(
            task_description=task_description,
            session_id=session_id,
            max_steps=max_steps,
        )
        _active_tasks[session_id] = state

        # 更新数据库
        async with _SessionFactory() as db:
            session_model = await db.get(AgentSessionModel, session_id)
            if session_model:
                status_map = {
                    AgentStatus.COMPLETED: SessionStatus.SUCCESS,
                    AgentStatus.FAILED: SessionStatus.FAILED,
                    AgentStatus.CANCELLED: SessionStatus.CANCELLED,
                }
                session_model.session_status = status_map.get(
                    state.agent_status, SessionStatus.FAILED,
                )
                session_model.current_step = state.total_steps_executed
                session_model.total_steps = len(state.plan_steps)
                session_model.llm_cost = state.total_llm_cost
                session_model.error_message = state.error_message
                session_model.finished_at = datetime.now(timezone.utc)
                session_model.execution_log = {
                    "total_steps": state.total_steps_executed,
                    "total_tokens": state.total_tokens_used,
                    "final_status": state.agent_status.value,
                }
                await db.commit()

        logger.info(
            "[Background] 任务执行完成: session_id=%d, status=%s, steps=%d",
            session_id, state.agent_status.value, state.total_steps_executed,
        )

    except Exception as exc:
        logger.error("[Background] 任务执行异常: session_id=%d, error=%s",
                    session_id, exc)
        # 更新数据库为失败状态
        async with _SessionFactory() as db:
            session_model = await db.get(AgentSessionModel, session_id)
            if session_model:
                session_model.session_status = SessionStatus.FAILED
                session_model.error_message = str(exc)
                session_model.finished_at = datetime.now(timezone.utc)
                await db.commit()
