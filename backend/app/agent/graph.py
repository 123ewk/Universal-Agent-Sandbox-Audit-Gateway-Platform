"""
AgentGraph — LangGraph Plan-Execute-Observe-Reflect 循环

设计动机：
  ReAct 模式（Reason+Act）容易陷入无限循环，因为 LLM 每次只决定"下一步做什么"。
  Plan-Execute-Reflect 模式先规划再执行，每步执行后评估结果，能更好地追踪进度。

状态机：
                    ┌─────────┐
                    │  START  │
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │  PLAN   │  LLM 拆解任务为步骤列表
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │ EXECUTE │  调用 AuditGateway 执行当前步骤的 Skill
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │ OBSERVE │  ObservationPipeline 处理执行结果
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │ REFLECT │  LLM 评估结果，决定下一步
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         continue/   complete/   abort/
          retry      (END)     (END)
              │
              └──→ EXECUTE

安全设计：
  — 每步执行前进行风险评估
  — 需要审批的步骤暂停等待
  — 最大步数硬限制（防止无限循环）
  — 连续失败 3 次强制 replan

使用方式：
  graph = AgentGraph(llm_client=llm, gateway=gateway)
  result = await graph.invoke(task_description="搜索今天的天气", session_id=1)
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.agent.compression import ContextCompressor
from app.agent.context import ContextManager
from app.agent.llm import LLMClient, LLMResponse
from app.agent.observation import ObservationPipeline
from app.agent.prompts import PromptBuilder
from app.agent.state import (
    AgentState,
    AgentStatus,
    ObservationRecord,
    PlanStep,
    StepRecord,
)
from app.engine.gateway import ApprovalRequired, AuditGateway
from app.skills.base import SkillContext
from app.skills.selector import SkillSelector

logger = logging.getLogger(__name__)

# 最大连续失败次数（超过后强制 replan）
_MAX_CONSECUTIVE_FAILURES = 3


class AgentGraph:
    """
    LangGraph Agent 编排图

    封装 Plan-Execute-Observe-Reflect 循环，
    通过 LangGraph StateGraph 管理状态流转。
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        gateway: Optional[AuditGateway] = None,
        context_manager: Optional[ContextManager] = None,
        observation_pipeline: Optional[ObservationPipeline] = None,
        compressor: Optional[ContextCompressor] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        checkpointer: Optional[Any] = None,
        sandbox_provider: Optional[Any] = None,    # Phase 6
        ws_manager: Optional[Any] = None,           # Phase 7
        detector: Optional[Any] = None,             # Phase 7
        approval_manager: Optional[Any] = None,     # Phase 7
    ) -> None:
        self.llm = llm_client or LLMClient()
        self.gateway = gateway or AuditGateway()
        self.context_manager = context_manager or ContextManager()
        self.observation_pipeline = observation_pipeline or ObservationPipeline()
        self.compressor = compressor or ContextCompressor()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.sandbox_provider = sandbox_provider
        self.ws_manager = ws_manager              # Phase 7
        self.detector = detector                  # Phase 7
        self.approval_manager = approval_manager  # Phase 7
        self.question_manager = None              # Phase 10: set by AgentRuntime

        self._checkpointer = checkpointer or MemorySaver()
        self._engines: dict[int, Any] = {}

        self._graph = self._build_graph()

    # ================================================================
    # 图构建
    # ================================================================

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph StateGraph"""
        workflow = StateGraph(AgentState)

        # 注册节点
        workflow.add_node("intent", self._intent_node)
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("observe", self._observe_node)
        workflow.add_node("reflect", self._reflect_node)

        # 边
        workflow.set_entry_point("intent")

        # intent → plan（正常）或 END（WAITING_USER）
        workflow.add_conditional_edges(
            "intent",
            self._route_after_intent,
            {
                "plan": "plan",
                "end": END,
            },
        )

        workflow.add_edge("plan", "execute")
        workflow.add_edge("execute", "observe")
        workflow.add_edge("observe", "reflect")

        # 条件边：Reflect → Execute / END
        workflow.add_conditional_edges(
            "reflect",
            self._route_after_reflect,
            {
                "execute": "execute",
                "replan": "plan",
                "end": END,
            },
        )

        return workflow.compile(checkpointer=self._checkpointer)

    # ================================================================
    # 节点实现
    # ================================================================

    async def _intent_node(self, state: AgentState) -> dict[str, Any]:
        """
        Intent 节点：LLM 分析任务意图

        流程：
          1. 构建 Intent Prompt
          2. 调用 LLM 输出结构化意图结果
          3. 如果有歧义 → 触发 WAITING_USER
          4. 写入 intent_result 到 state
        """
        logger.info("[Intent] 分析任务意图: %s", state.task_description[:80])
        state.transition_to(AgentStatus.ANALYZING)

        # 推送 analyzing 状态到前端
        if self.ws_manager:
            await self.ws_manager.broadcast_message(
                state.session_id,
                {
                    "session_id": state.session_id,
                    "event": "agent.planning",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {},
                },
            )

        try:
            intent_prompt = self.prompt_builder.build_intent_prompt(
                state.task_description,
            )
            messages = [
                {"role": "system", "content": self.prompt_builder.build_system()},
                {"role": "user", "content": intent_prompt},
            ]

            response = await self.llm.chat(messages, tool_choice="none")
            intent_json = self.llm._extract_json_object(response.content)

            # 解析为 IntentResult
            from app.agent.state import IntentResult
            intent = IntentResult(
                intent_category=intent_json.get("intent_category", "GENERAL_QA"),
                confidence=float(intent_json.get("confidence", 0.5)),
                clarifying_questions=intent_json.get("clarifying_questions", []),
                suggested_tools=intent_json.get("suggested_tools", []),
                reasoning=intent_json.get("reasoning", ""),
                reasoning_chain=intent_json.get("reasoning_chain", []),
            )
            state.intent_result = intent
            state.add_cost(response.cost, response.tokens_used)

            # 广播 agent.thought 事件
            if self.ws_manager:
                from app.ws.protocol import ThoughtPayload, agent_thought
                await self.ws_manager.broadcast_message(
                    state.session_id,
                    agent_thought(state.session_id, ThoughtPayload(
                        thought=intent.reasoning,
                        intent=intent.intent_category,
                        confidence=intent.confidence,
                        reasoning_chain=intent.reasoning_chain,
                        step_number=0,
                    )).to_dict(),
                )

            logger.info(
                "[Intent] 意图分析完成: category=%s, confidence=%.0f%%, questions=%d",
                intent.intent_category, intent.confidence * 100, len(intent.clarifying_questions),
            )

            # 如果有歧义 → 暂停等待用户回答
            if intent.has_questions and self.question_manager:
                state.transition_to(AgentStatus.WAITING_USER)
                first_q = intent.clarifying_questions[0]
                question_text = first_q.get("question", "")
                options = first_q.get("options", [])

                # 推送 question 事件到前端
                if self.ws_manager:
                    from app.ws.protocol import QuestionPayload, agent_question
                    await self.ws_manager.broadcast_message(
                        state.session_id,
                        agent_question(state.session_id, QuestionPayload(
                            question_id=self.question_manager._next_id,
                            question_text=question_text,
                            options=options,
                            context={"intent": intent.intent_category},
                        )).to_dict(),
                    )

                # 等待用户回答（asyncio.Event 暂停）
                logger.info("[Intent] 需要用户澄清，暂停等待回答: %s", question_text[:80])
                question = await self.question_manager.ask(
                    session_id=state.session_id,
                    question_text=question_text,
                    options=options,
                    context={"intent": intent.intent_category, "reasoning": intent.reasoning},
                )

                # 用户已回答，恢复执行
                state.transition_to(AgentStatus.ANALYZING)
                if question.status == "answered":
                    logger.info("[Intent] 用户已回答: '%s'", question.answer)
                    # 将用户回答附加到任务描述中
                    state.task_description = (
                        f"{state.task_description}\n(用户澄清: {question.answer})"
                    )
                else:
                    logger.info("[Intent] 用户跳过问题 (status=%s)", question.status)

            elif intent.has_questions:
                # 无 question_manager 时仅广播（向后兼容）
                state.current_question = {
                    "question_id": 1,
                    "questions": intent.clarifying_questions,
                    "reasoning": intent.reasoning,
                }
                if self.ws_manager:
                    from app.ws.protocol import QuestionPayload, agent_question
                    first_q = intent.clarifying_questions[0]
                    await self.ws_manager.broadcast_message(
                        state.session_id,
                        agent_question(state.session_id, QuestionPayload(
                            question_id=1,
                            question_text=first_q.get("question", ""),
                            options=first_q.get("options", []),
                            context={"intent": intent.intent_category},
                        )).to_dict(),
                    )
                logger.info("[Intent] 存在用户澄清建议 (无暂停机制): %d 个问题",
                           len(intent.clarifying_questions))

        except Exception as exc:
            logger.error("[Intent] 意图分析失败: %s", exc)
            # 意图分析失败不致命，创建默认意图继续
            from app.agent.state import IntentResult
            state.intent_result = IntentResult(
                intent_category="GENERAL_QA",
                confidence=0.5,
                reasoning=f"意图分析失败: {exc}",
            )

        return {
            "intent_result": state.intent_result,
            "agent_status": state.agent_status,
            "current_question": state.current_question,
            "total_llm_cost": str(state.total_llm_cost),
            "total_tokens_used": state.total_tokens_used,
        }

    async def _plan_node(self, state: AgentState) -> dict[str, Any]:
        """
        Plan 节点：LLM 拆解任务为执行步骤

        流程：
          1. 标记状态为 PLANNING
          2. 构建 Plan Prompt
          3. 调用 LLM 生成步骤列表
          4. 解析 JSON → PlanStep 列表
          5. 初始化 SkillSelector 并解锁必要 Tier
        """
        logger.info("[Plan] 开始规划任务: %s", state.task_description[:80])
        state.transition_to(AgentStatus.PLANNING)

        # 推送 planning 事件到前端
        if self.ws_manager:
            await self.ws_manager.broadcast_message(
                state.session_id,
                {"session_id": state.session_id, "event": "agent.planning",
                 "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {}},
            )

        # 如果已经失败太多次，直接返回失败
        if state.total_steps_executed >= state.max_steps:
            state.error_message = f"超过最大执行步数 ({state.max_steps})"
            state.transition_to(AgentStatus.FAILED)
            return {"agent_status": state.agent_status, "error_message": state.error_message}

        try:
            # 获取可用工具列表（初始为 CORE）
            selector = SkillSelector()
            tools = selector.get_llm_tools()

            # 构建 Plan Prompt
            plan_prompt = self.prompt_builder.build_plan_prompt(
                task_description=state.task_description,
                state=state,
                selector=selector,
            )

            messages = [
                {"role": "system", "content": self.prompt_builder.build_system()},
                {"role": "user", "content": plan_prompt},
            ]

            response = await self.llm.chat(messages, tools=tools, tool_choice="none")
            plan_json = self.llm._extract_json_array(response.content)

            # 解析为 PlanStep
            plan_steps: list[PlanStep] = []
            for item in plan_json:
                try:
                    step = PlanStep(
                        step_number=item.get("step_number", len(plan_steps) + 1),
                        description=item.get("description", ""),
                        skill_name=item.get("skill_name", ""),
                        skill_params=item.get("skill_params", {}),
                        expected_outcome=item.get("expected_outcome", ""),
                        required_tier=item.get("required_tier", "CORE"),
                        thought=item.get("thought", ""),
                        reasoning_chain=item.get("reasoning_chain", []),
                    )
                    plan_steps.append(step)
                except Exception as exc:
                    logger.warning("跳过无效步骤: %s, error=%s", item, exc)

            state.plan_steps = plan_steps
            state.total_steps_planned = len(plan_steps)
            state.current_step_index = 0
            state.add_cost(response.cost, response.tokens_used)

            logger.info(
                "[Plan] 规划完成: %d 步骤, cost=$%s",
                len(plan_steps), response.cost,
            )

            # 推送 plan.completed 事件到前端（包含步骤列表）
            if self.ws_manager:
                await self.ws_manager.broadcast_message(
                    state.session_id,
                    {
                        "session_id": state.session_id,
                        "event": "agent.plan.completed",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "payload": {
                            "steps": [step.model_dump() for step in plan_steps],
                            "total_steps": len(plan_steps),
                        },
                    },
                )

                # 广播 agent.thought 事件 — 每步的思考过程
                from app.ws.protocol import ThoughtPayload, agent_thought as at_func
                for step in plan_steps:
                    thought_text = getattr(step, "thought", None)
                    reasoning = getattr(step, "reasoning_chain", None)
                    if thought_text or reasoning:
                        await self.ws_manager.broadcast_message(
                            state.session_id,
                            at_func(state.session_id, ThoughtPayload(
                                thought=thought_text or step.description,
                                intent=state.intent_result.intent_category if state.intent_result else "",
                                confidence=state.intent_result.confidence if state.intent_result else 0.5,
                                reasoning_chain=reasoning or [],
                                step_number=step.step_number,
                            )).to_dict(),
                        )

        except Exception as exc:
            logger.error("[Plan] 规划失败: %s", exc)
            state.error_message = f"规划失败: {exc}"
            state.transition_to(AgentStatus.FAILED)

        return {
            "plan_steps": state.plan_steps,
            "total_steps_planned": state.total_steps_planned,
            "agent_status": state.agent_status,
            "total_llm_cost": str(state.total_llm_cost),
            "total_tokens_used": state.total_tokens_used,
            "needs_replan": False,  # 重置 replan 标记
        }

    async def _execute_node(self, state: AgentState) -> dict[str, Any]:
        """
        Execute 节点：调用 AuditGateway 执行当前步骤的 Skill

        流程：
          1. 获取当前步骤
          2. 检测需要解锁的 Tier → 解锁
          3. 加载 skill.md 文档
          4. 通过 AuditGateway.invoke() 执行
          5. 处理审批等待（ApprovalRequired）
          6. 记录 StepRecord
        """
        # 如果前置节点已将状态置为终态，直接跳过
        if state.is_finished:
            return {}

        step = state.current_plan_step
        if step is None:
            state.error_message = "没有可执行的步骤"
            state.transition_to(AgentStatus.FAILED)
            return {"agent_status": state.agent_status, "error_message": state.error_message}

        logger.info("[Execute] Step %d/%d: %s",
                    state.current_step_index + 1, len(state.plan_steps),
                    step.description)

        state.transition_to(AgentStatus.EXECUTING)

        # 解锁所需 Tier
        selector = SkillSelector()
        if step.required_tier:
            from app.skills.enums import SkillTier
            try:
                tier = SkillTier(step.required_tier.lower())
                selector.unlock(tier)
            except ValueError:
                logger.warning("无效的 Tier: %s", step.required_tier)

        # 创建执行上下文（注入 SandboxEngine）
        engine = self._engines.get(state.session_id)
        context = SkillContext(
            session_id=state.session_id,
            request_id=f"step_{state.current_step_index + 1}",
            sandbox_engine=engine,  # Phase 6: Skills 通过此字段操作浏览器
        )

        started_at = datetime.now(timezone.utc)
        record = StepRecord(
            step_number=step.step_number,
            plan_step=step,
            started_at=started_at,
        )

        try:
            # 通过 AuditGateway 安全执行
            result = await self.gateway.invoke(
                skill_name=step.skill_name,
                params=step.skill_params,
                context=context,
                db=None,  # TODO: 集成数据库 session
            )

            # 处理审批等待
            if isinstance(result, ApprovalRequired):
                record.required_approval = True
                record.success = False
                record.error_message = "等待人类审批"
                state.transition_to(AgentStatus.WAITING_APPROVAL)

                # Phase 7: 通过 WebSocket 推送审批通知
                if self.ws_manager and self.approval_manager:
                    from app.ws.protocol import ApprovalPayload, approval_required
                    approval_req = await self.approval_manager.request(
                        session_id=state.session_id,
                        skill_name=step.skill_name,
                        step_number=step.step_number,
                        risk_score=result.assessment.score if result.assessment else 0,
                    )
                    await self.ws_manager.broadcast_message(
                        state.session_id,
                        approval_required(state.session_id, ApprovalPayload(
                            approval_id=approval_req.id,
                            skill_name=step.skill_name,
                            risk_score=result.assessment.score if result.assessment else 0,
                            step_number=step.step_number,
                        )).to_dict(),
                    )
                    # 等待审批结果
                    await approval_req.event.wait()
                    if approval_req.status.value == "approved":
                        # 审批通过，重新执行（bypass_approval）
                        pass  # 继续执行
                    else:
                        record.error_message = f"审批被拒绝: {approval_req.status.value}"
            else:
                record.success = result.success
                record.result_data = result.data
                record.error_message = result.error
                record.execution_time_ms = result.execution_time_ms

        except Exception as exc:
            logger.error("[Execute] Step %d 执行异常: %s", step.step_number, exc)
            record.success = False
            record.error_message = f"{type(exc).__name__}: {exc}"

        record.finished_at = datetime.now(timezone.utc)
        if record.started_at:
            delta = record.finished_at - record.started_at
            record.execution_time_ms = int(delta.total_seconds() * 1000)

        state.record_step(record)

        logger.info(
            "[Execute] Step %d 完成: success=%s, time=%dms",
            step.step_number, record.success, record.execution_time_ms,
        )

        # Phase 7: WebSocket 推送步骤完成事件
        if self.ws_manager:
            from app.ws.protocol import (
                StepPayload, ScreenshotPayload,
                agent_step_completed, agent_step_failed,
                sandbox_screenshot,
            )
            payload = StepPayload(
                step_number=step.step_number,
                skill_name=step.skill_name,
                description=step.description,
                success=record.success,
                execution_time_ms=record.execution_time_ms,
                error=record.error_message,
                result_data=record.result_data if record.success else None,
            )
            msg = agent_step_completed(state.session_id, payload) if record.success \
                else agent_step_failed(state.session_id, payload)
            await self.ws_manager.broadcast_message(state.session_id, msg.to_dict())

            # 截图/文本提取成功后，额外推送 sandbox.screenshot / sandbox.page.info 事件
            if record.success and record.result_data:
                data = record.result_data
                if step.skill_name == "browser_screenshot" and data.get("filename"):
                    ss_payload = ScreenshotPayload(
                        path=data.get("path", ""),
                        filename=data["filename"],
                        size_bytes=data.get("size_bytes", 0),
                        step_number=step.step_number,
                    )
                    await self.ws_manager.broadcast_message(
                        state.session_id,
                        sandbox_screenshot(state.session_id, ss_payload).to_dict(),
                    )
                elif step.skill_name == "browser_extract_text" and data.get("text"):
                    await self.ws_manager.broadcast_message(
                        state.session_id,
                        {
                            "session_id": state.session_id,
                            "event": "sandbox.page.info",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "payload": {
                                "text_preview": data["text"][:500],
                                "text_length": data.get("text_length", 0),
                                "selector": data.get("selector", "body"),
                            },
                        },
                    )

        return {
            "execution_history": state.execution_history,
            "total_steps_executed": state.total_steps_executed,
            "agent_status": state.agent_status,
            "total_llm_cost": str(state.total_llm_cost),
        }

    async def _observe_node(self, state: AgentState) -> dict[str, Any]:
        """
        Observe 节点：处理执行结果，生成结构化观察

        流程：
          1. 获取上一步的执行结果
          2. 通过 ObservationPipeline 处理（如结果中含 HTML）
          3. 生成 ObservationRecord
          4. 增量更新 observation_summary（借助 ContextCompressor）
          5. 裁剪 Working Context（如超限）
        """
        logger.info("[Observe] 处理观察数据")

        # 如果前置节点已将状态置为终态，直接跳过
        if state.is_finished:
            return {}

        state.transition_to(AgentStatus.OBSERVING)

        last_step = state.last_step
        if last_step is None:
            return {"agent_status": state.agent_status}

        # Phase 6: 优先从 SandboxEngine 获取真实页面信息
        engine = self._engines.get(state.session_id)
        if engine and engine.page:
            page_info = await engine.get_page_info()
            # engine.get_page_info() 已返回 cleaned_text + interactive_elements
            # 不暴露完整原始 HTML（上下文铁律）
            observation = ObservationRecord(
                summary=self._build_page_summary(page_info),
                page_title=page_info.title,
                page_url=page_info.url,
                interactive_elements=page_info.interactive_elements[:20],
                raw_data_ref=page_info.screenshot_path,
            )
        elif last_step.result_data and isinstance(last_step.result_data, dict):
            raw_html = last_step.result_data.get("html", "")
            if raw_html:
                observation = await self.observation_pipeline.process(
                    raw_html=raw_html,
                    page_url=last_step.result_data.get("url", ""),
                    page_title=last_step.result_data.get("title", ""),
                )
            else:
                observation = ObservationRecord(
                    summary=str(last_step.result_data)[:200],
                    page_url=last_step.result_data.get("url", ""),
                    page_title=last_step.result_data.get("title", ""),
                )
        else:
            observation = ObservationRecord(
                summary="执行完成",
            )

        state.last_observation = observation

        # 增量更新摘要
        self.compressor.update_summary(
            state=state,
            step=last_step,
            observation_summary=observation.summary,
        )

        # 裁剪 Working Context
        self.compressor.compress(state)

        return {
            "last_observation": state.last_observation,
            "observation_summary": state.observation_summary,
            "agent_status": state.agent_status,
            "execution_history": state.execution_history,
        }

    async def _reflect_node(self, state: AgentState) -> dict[str, Any]:
        """
        Reflect 节点：LLM 评估执行结果，决定下一步

        决策：
          continue: 执行下一步
          retry:    重试当前步骤（修改参数）
          replan:   重新规划
          complete: 任务完成
          abort:    无法继续

        自动决策（不调 LLM）：
          - 等待审批 → 暂停
          - 已完成所有步骤 → complete
          - 连续失败 N 次 → replan 或 abort
        """
        logger.info("[Reflect] 评估执行结果")

        # 如果前置节点已将状态置为终态，直接跳过
        if state.is_finished:
            return {}

        state.transition_to(AgentStatus.REFLECTING)

        # Phase 7: 跨步骤行为检测
        if self.detector:
            assessment = await self.detector.analyze(state, self.ws_manager)
            if assessment.should_pause and self.approval_manager:
                logger.warning(
                    "[Reflect] 行为检测触发暂停: session=%d, score=%d",
                    state.session_id, assessment.total_score,
                )

        # 自动决策：等待审批
        if state.agent_status == AgentStatus.WAITING_APPROVAL:
            if self.approval_manager:
                # Phase 7: 审批已通过 execute 中完成，此处恢复执行
                logger.info("[Reflect] 审批流程已完成，恢复执行")
                state.agent_status = AgentStatus.EXECUTING
                state.current_step_index += 1
            else:
                # 向后兼容：无审批管理器时自动通过
                logger.info("[Reflect] 等待审批 — 开发模式自动通过")
                state.agent_status = AgentStatus.EXECUTING
                state.current_step_index += 1

            # 检查是否所有步骤完成
            if state.current_step_index >= len(state.plan_steps):
                state.transition_to(AgentStatus.COMPLETED)
                return {"agent_status": state.agent_status, "needs_replan": False}

            return {"agent_status": state.agent_status, "needs_replan": False}

        # 自动决策：成功 → 自动继续或完成（不调 LLM，节省成本）
        last_step = state.last_step
        if last_step and last_step.success:
            if state.current_step_index >= len(state.plan_steps) - 1:
                state.transition_to(AgentStatus.COMPLETED)
                logger.info("[Reflect] 所有步骤执行完成")
                return {"agent_status": state.agent_status, "needs_replan": False}
            else:
                # 成功且还有后续步骤 → 自动继续，不调 LLM
                logger.info("[Reflect] Step %d 成功，自动继续", last_step.step_number)
                state.current_step_index += 1
                return {"current_step_index": state.current_step_index, "needs_replan": False}

        # 自动决策：连续失败检测
        recent = state.execution_history[-_MAX_CONSECUTIVE_FAILURES:]
        if len(recent) >= _MAX_CONSECUTIVE_FAILURES and all(not s.success for s in recent):
            if state.current_step_index > 0:
                logger.warning("[Reflect] 连续 %d 次失败，强制 replan", _MAX_CONSECUTIVE_FAILURES)
                return {"agent_status": state.agent_status, "needs_replan": True}
            else:
                logger.error("[Reflect] 第一步就连续失败，放弃")
                state.transition_to(AgentStatus.FAILED)
                state.error_message = f"连续 {_MAX_CONSECUTIVE_FAILURES} 次失败"
                return {"agent_status": state.agent_status, "needs_replan": False}

        # 调用 LLM 评估
        try:
            reflect_prompt = self.prompt_builder.build_reflect_prompt(state)
            decision = await self.llm.reflect(reflect_prompt)

            decision_type = decision.get("decision", "continue")
            reason = decision.get("reason", "")
            logger.info("[Reflect] LLM 决策: %s — %s", decision_type, reason)

        except Exception as exc:
            logger.error("[Reflect] LLM 评估失败: %s, 默认 continue", exc)
            decision_type = "continue"

        # 根据决策执行
        if decision_type == "continue":
            state.current_step_index += 1
            if state.current_step_index >= len(state.plan_steps):
                if state.has_any_success:
                    state.transition_to(AgentStatus.COMPLETED)
                else:
                    state.transition_to(AgentStatus.FAILED)
                    state.error_message = "所有步骤执行失败，无法完成任务"
                    logger.error("[Reflect] 所有 %d 步均失败，标记为 FAILED", len(state.plan_steps))
                return {"agent_status": state.agent_status, "needs_replan": False}
            return {"current_step_index": state.current_step_index, "needs_replan": False}

        elif decision_type == "retry":
            # 重试当前步骤（不推进 current_step_index）
            modified_params = decision.get("modified_params", {})
            if modified_params and state.current_plan_step:
                state.current_plan_step.skill_params.update(modified_params)
            return {"needs_replan": False}

        elif decision_type == "replan":
            # 重新规划（保留进度，重新生成后续步骤）
            remaining = state.task_description
            if state.observation_summary:
                remaining += f"\n(当前进度: {state.observation_summary})"
            state.task_description = remaining
            return {"needs_replan": True}

        elif decision_type == "complete":
            if state.has_any_success:
                state.transition_to(AgentStatus.COMPLETED)
            else:
                state.transition_to(AgentStatus.FAILED)
                state.error_message = "LLM 判定完成但无任何成功步骤"
            return {"agent_status": state.agent_status, "needs_replan": False}

        else:  # abort
            state.transition_to(AgentStatus.FAILED)
            state.error_message = decision.get("reason", "LLM 决定中止执行")
            return {"agent_status": state.agent_status, "error_message": state.error_message, "needs_replan": False}

    # ================================================================
    # 条件路由
    # ================================================================

    @staticmethod
    def _route_after_intent(
        state: AgentState,
    ) -> Literal["plan", "end"]:
        """
        根据 Intent 节点的结果，决定下一个节点

        路由逻辑：
          - is_finished → end
          - 其他 → plan（Phase 10B 实现 WAITING_USER 暂停）
        """
        if state.is_finished:
            return "end"
        return "plan"

    @staticmethod
    def _route_after_reflect(
        state: AgentState,
    ) -> Literal["execute", "replan", "end"]:
        """
        根据 Reflect 节点的决策，决定下一个节点

        路由逻辑：
          - is_finished → end
          - needs_replan → replan
          - 还有未完成的步骤 → execute
          - 否则 → end
        """
        if state.is_finished:
            return "end"

        if state.needs_replan:
            return "replan"

        if state.current_step_index < len(state.plan_steps):
            return "execute"
        return "end"

    # ================================================================
    # 公共接口
    # ================================================================

    @staticmethod
    def _build_page_summary(page_info: Any) -> str:
        """从 PageInfo 构建观察摘要"""
        parts = [f"页面 '{page_info.title}'" if page_info.title else "当前页面"]
        if page_info.element_count > 0:
            parts.append(f"含 {page_info.element_count} 个交互元素")
        if page_info.screenshot_path:
            parts.append(f"截图: {page_info.screenshot_path}")
        return "，".join(parts)

    async def invoke(
        self,
        task_description: str,
        session_id: int = 0,
        max_steps: int = 50,
    ) -> AgentState:
        """
        执行一个 Agent 任务

        Args:
            task_description: 用户自然语言任务描述
            session_id:       数据库会话 ID
            max_steps:        最大执行步数

        Returns:
            执行结束时的 AgentState
        """
        initial_state = AgentState(
            session_id=session_id,
            task_description=task_description,
            max_steps=max_steps,
        )

        logger.info("[AgentGraph] 开始执行任务: session=%d, task='%s'",
                    session_id, task_description[:80])

        # Phase 6: 创建 SandboxEngine（如配置了 Provider）
        engine = None
        if self.sandbox_provider:
            from app.sandbox.engine import SandboxEngine
            engine = SandboxEngine(
                provider=self.sandbox_provider,
                session_id=session_id,
            )
            await engine.create_context()
            self._engines[session_id] = engine
            logger.info("[AgentGraph] SandboxEngine 已创建: session=%d", session_id)

        try:
            # LangGraph ainvoke 自动管理状态流转
            final_state = await self._graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": str(session_id)}},
            )

            # ainvoke 返回 dict，转换为 AgentState（如果是 dict）
            if isinstance(final_state, dict):
                result = AgentState(**final_state)
            else:
                result = final_state

            logger.info(
                "[AgentGraph] 任务执行完成: session=%d, status=%s, "
                "steps=%d, cost=$%s",
                session_id, result.agent_status.value,
                result.total_steps_executed, result.total_llm_cost,
            )
            return result

        except Exception as exc:
            logger.error("[AgentGraph] 任务执行异常: session=%d, error=%s",
                        session_id, exc)
            return AgentState(
                session_id=session_id,
                task_description=task_description,
                agent_status=AgentStatus.FAILED,
                error_message=str(exc),
            )
        finally:
            # Phase 6: 清理 SandboxEngine（Phase 10: 根据配置决定是否自动关闭）
            if engine:
                from app.config import settings
                if settings.SANDBOX_AUTO_CLOSE:
                    await engine.cleanup()
                    self._engines.pop(session_id, None)
                    logger.info("[AgentGraph] SandboxEngine 已清理: session=%d", session_id)
                else:
                    logger.info("[AgentGraph] SandboxEngine 保持活跃 (auto_close=False): session=%d", session_id)

            # 注：WS cleanup 由 AgentRuntime.run() 统一处理，此处不重复调用
