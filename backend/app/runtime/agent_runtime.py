"""
AgentRuntime — Agent 执行运行时

职责：
  1. 封装 AgentGraph.invoke() 为标准化的 Runtime.run()
  2. 执行前/中/后通过 EventBus 发出事件
  3. 通过 SessionManager 维护 Session 状态

数据流：
  TaskManager.start(coro) → AgentRuntime.run()
    → EventBus.dispatch("agent.started")
    → AgentGraph.invoke()
    → EventBus.dispatch("agent.completed" | "agent.failed")
    → SessionManager.update(status, progress)

使用方式：
  runtime = AgentRuntime(llm_client=LLMClient(), gateway=AuditGateway())
  state = await runtime.run(session_id=42, task="打开百度")
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.agent.graph import AgentGraph
from app.agent.llm import LLMClient
from app.agent.state import AgentState
from app.engine.gateway import AuditGateway
from app.runtime.event_bus import get_event_bus
from app.runtime.session_manager import get_session_manager
from app.runtime.websocket_manager import get_websocket_manager

logger = logging.getLogger(__name__)


class AgentRuntime:
    """
    Agent 执行运行时

    封装 AgentGraph，在关键生命周期节点发出事件：
      - 启动时：agent.started
      - 规划完成：agent.plan.completed
      - 步骤完成：agent.step.completed（AgentGraph 内部通过 ws_manager 推送）
      - 完成：agent.completed
      - 失败：agent.failed
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        gateway: Optional[AuditGateway] = None,
        sandbox_provider: Optional[Any] = None,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.gateway = gateway or AuditGateway()
        self.sandbox_provider = sandbox_provider

    # ================================================================
    # 主入口
    # ================================================================

    async def run(
        self,
        session_id: int,
        task_description: str,
        max_steps: int = 50,
    ) -> AgentState:
        """
        执行 Agent 任务

        Args:
            session_id:       Session ID
            task_description: 任务描述
            max_steps:        最大执行步数

        Returns:
            执行结束时的 AgentState
        """
        sm = get_session_manager()
        bus = get_event_bus()
        wsm = get_websocket_manager()

        # 确保 Session 已创建
        if not sm.exists(session_id):
            sm.create(session_id, task_description, total_steps=max_steps)

        sm.update(session_id, status="running")

        # 发出 start 事件
        await bus.dispatch(
            session_id,
            "agent.started",
            {"task_description": task_description, "max_steps": max_steps},
            seq=wsm.get_current_seq(session_id) + 1,
        )

        # AndroidAgentGraph 的 ws_manager 传入 WebSocketManager，
        # 使得执行过程（Plan/Execute/Observe/Reflect）中的事件能实时推送到前端
        graph = AgentGraph(
            llm_client=self.llm_client,
            gateway=self.gateway,
            sandbox_provider=self.sandbox_provider,
            ws_manager=wsm,
        )

        try:
            state = await graph.invoke(
                task_description=task_description,
                session_id=session_id,
                max_steps=max_steps,
            )

            # 同步最终状态到 SessionManager
            sm.update(
                session_id,
                status=state.agent_status.value,
                total_steps=len(state.plan_steps),
                total_steps_executed=state.total_steps_executed,
                progress_pct=state.progress_pct,
                llm_cost=str(state.total_llm_cost),
                tokens_used=state.total_tokens_used,
                error_message=state.error_message,
            )

            # 持久化到 PostgreSQL
            await _persist_session(session_id, state)

            # 完成事件
            if state.agent_status.value == "completed":
                await bus.dispatch(
                    session_id,
                    "agent.completed",
                    {
                        "total_steps": state.total_steps_executed,
                        "tokens_used": state.total_tokens_used,
                        "llm_cost": str(state.total_llm_cost),
                    },
                    seq=wsm.get_current_seq(session_id) + 1,
                )
            else:
                await bus.dispatch(
                    session_id,
                    "agent.failed",
                    {
                        "error": state.error_message or "未知错误",
                        "total_steps": state.total_steps_executed,
                    },
                    seq=wsm.get_current_seq(session_id) + 1,
                )

            return state

        except asyncio.CancelledError:
            sm.update(session_id, status="cancelled")
            await bus.dispatch(
                session_id,
                "agent.cancelled",
                {"reason": "用户取消"},
                seq=wsm.get_current_seq(session_id) + 1,
            )
            raise

        except Exception as exc:
            sm.update(session_id, status="failed", error_message=str(exc))
            await bus.dispatch(
                session_id,
                "agent.failed",
                {"error": str(exc)},
                seq=wsm.get_current_seq(session_id) + 1,
            )
            logger.error("[Runtime] Agent 执行异常: session=%d, error=%s", session_id, exc)
            raise

        finally:
            await wsm.cleanup_session(session_id)


# ====================================================================
# DB 持久化
# ====================================================================

async def _persist_session(session_id: int, state: AgentState) -> None:
    """将最终 AgentState 写入 PostgreSQL"""
    try:
        from app.database import _SessionFactory
        from app.models.session import AgentSession as AgentSessionModel, SessionStatus

        status_map = {
            "completed": SessionStatus.SUCCESS,
            "failed": SessionStatus.FAILED,
            "cancelled": SessionStatus.CANCELLED,
        }
        final_status = status_map.get(state.agent_status.value, SessionStatus.FAILED)

        async with _SessionFactory() as db:
            session_model = await db.get(AgentSessionModel, session_id)
            if session_model:
                session_model.session_status = final_status
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
    except Exception as exc:
        logger.error("DB 持久化失败: session=%d, error=%s", session_id, exc)
