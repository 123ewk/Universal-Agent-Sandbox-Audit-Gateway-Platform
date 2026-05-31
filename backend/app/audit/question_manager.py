"""
QuestionManager — Agent 向人类提问管理器

设计动机：
  Agent 遇到歧义或需要人类判断时，不应直接做决策，
  而应通过 QuestionManager 向用户提问并暂停等待回答。

不同于 ApprovalManager：
  - ApprovalManager: 安全审批（L4 风险操作需要人类批准）
  - QuestionManager:  歧义澄清（Agent 不确定如何执行时向用户提问）

核心机制：
  1. Agent 调用 question_manager.ask() 创建一个 Question
  2. Agent 协程 await question.event.wait() ← 暂停
  3. 前端展示问题，用户选择/输入回答
  4. REST API 调用 question_manager.answer() → event.set() → Agent 恢复
  5. Agent 读取 question.answer 继续执行

使用方式：
  qm = get_question_manager()
  question = await qm.ask(session_id=42, question_text="你想要哪个版本?", options=["A", "B"])
  user_answer = question.answer  # "A"
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentQuestion:
    """Agent 向人类提出的一个问题"""
    id: int
    session_id: int
    question_text: str
    options: list[str]
    context: dict[str, Any]
    step_number: int = 0
    status: str = "pending"  # "pending" | "answered" | "skipped" | "timeout"
    answer: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    # asyncio.Event 用于 Agent 暂停/恢复
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class QuestionManager:
    """
    问题管理器

    管理 Agent 向用户提问的生命周期：
      ask() → Agent await → answer()/skip() → Agent resume
    """

    def __init__(self, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self._questions: dict[int, AgentQuestion] = {}
        self._next_id: int = 1

    # ================================================================
    # 提问
    # ================================================================

    async def ask(
        self,
        session_id: int,
        question_text: str,
        options: Optional[list[str]] = None,
        context: Optional[dict[str, Any]] = None,
        step_number: int = 0,
    ) -> AgentQuestion:
        """
        创建一个问题并等待用户回答

        Agent 调用此方法后会阻塞在此，
        直到 answer() / skip() / timeout 唤醒。
        """
        q = AgentQuestion(
            id=self._next_id,
            session_id=session_id,
            question_text=question_text,
            options=options or [],
            context=context or {},
            step_number=step_number,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.timeout_seconds),
        )
        self._questions[q.id] = q
        self._next_id += 1

        logger.info(
            "Agent 提问: id=%d, session=%d, question='%s', timeout=%ds",
            q.id, session_id, question_text[:80], self.timeout_seconds,
        )

        try:
            await asyncio.wait_for(
                q.event.wait(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            q.status = "timeout"
            q.resolved_at = datetime.now(timezone.utc)
            logger.warning("Agent 提问超时: id=%d", q.id)

        return q

    # ================================================================
    # 回答
    # ================================================================

    async def answer(self, question_id: int, answer_text: str) -> AgentQuestion:
        """
        回答 Agent 的问题
        """
        q = self._get_question(question_id)
        q.status = "answered"
        q.answer = answer_text
        q.resolved_at = datetime.now(timezone.utc)
        q.event.set()
        logger.info("问题已回答: id=%d, answer='%s'", question_id, answer_text[:60])
        return q

    async def skip(self, question_id: int) -> AgentQuestion:
        """
        跳过问题（用户选择不回答）
        """
        q = self._get_question(question_id)
        q.status = "skipped"
        q.resolved_at = datetime.now(timezone.utc)
        q.event.set()
        logger.info("问题已跳过: id=%d", question_id)
        return q

    # ================================================================
    # 查询
    # ================================================================

    def get_question(self, question_id: int) -> Optional[AgentQuestion]:
        return self._questions.get(question_id)

    def get_pending(self, session_id: Optional[int] = None) -> list[AgentQuestion]:
        pending = [q for q in self._questions.values() if q.status == "pending"]
        if session_id is not None:
            pending = [q for q in pending if q.session_id == session_id]
        return pending

    def get_history(self, session_id: Optional[int] = None) -> list[AgentQuestion]:
        resolved = [q for q in self._questions.values() if q.status != "pending"]
        if session_id is not None:
            resolved = [q for q in resolved if q.session_id == session_id]
        return resolved

    def _get_question(self, question_id: int) -> AgentQuestion:
        q = self._questions.get(question_id)
        if q is None:
            raise ValueError(f"问题不存在: {question_id}")
        if q.is_expired:
            raise ValueError(f"问题已过期: {question_id}")
        return q


# ====================================================================
# 全局单例
# ====================================================================

_question_manager: Optional[QuestionManager] = None


def get_question_manager() -> QuestionManager:
    global _question_manager
    if _question_manager is None:
        _question_manager = QuestionManager()
    return _question_manager


def set_question_manager(mgr: QuestionManager) -> None:
    global _question_manager
    _question_manager = mgr
