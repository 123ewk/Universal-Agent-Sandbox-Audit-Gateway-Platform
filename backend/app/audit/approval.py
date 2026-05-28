"""
ApprovalManager — 审批暂停/恢复系统

设计动机：
  Agent 执行高危操作时，必须暂停等待人类审批。
  使用 asyncio.Event 实现非阻塞暂停机制，
  比轮询优雅，比回调模式简单。

核心机制：
  1. Agent _execute_node 执行前检查是否需要审批
  2. 需要审批 → 创建 ApprovalRecord + asyncio.Event
  3. Agent 节点 await event.wait()  ← 阻塞但不占 CPU
  4. 前端审批 API 被调用 → event.set() 或 approval_denied
  5. Agent 节点恢复执行或中止

超时处理：
  审批请求有超时时间（默认 5 分钟），超时后自动拒绝。

使用方式：
  approval_mgr = ApprovalManager(timeout_seconds=300)
  approval_id = await approval_mgr.request(session_id, skill_name, risk_score)
  # Agent 暂停...
  await approval_mgr.approve(approval_id)  # 外部 API 调用
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"


@dataclass
class ApprovalRequest:
    """单个审批请求"""
    id: int
    session_id: int
    skill_name: str
    step_number: int = 0
    risk_score: int = 0
    risk_reasons: list[str] = field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""

    # asyncio.Event 用于 Agent 暂停/恢复
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class ApprovalManager:
    """
    审批管理器

    管理审批请求的生命周期：
      request() → Agent await → approve()/deny() → Agent resume
    """

    def __init__(self, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self._requests: dict[int, ApprovalRequest] = {}
        self._next_id: int = 1

    # ================================================================
    # 审批请求
    # ================================================================

    async def request(
        self,
        session_id: int,
        skill_name: str,
        step_number: int = 0,
        risk_score: int = 0,
        risk_reasons: Optional[list[str]] = None,
    ) -> ApprovalRequest:
        """
        创建一个审批请求并等待结果

        Agent 调用此方法后会阻塞在此，
        直到 approve() / deny() / timeout 唤醒。

        Args:
            session_id:   Agent Session ID
            skill_name:   触发审批的 Skill 名称
            step_number:  当前步骤号
            risk_score:   风险评分
            risk_reasons: 风险原因列表

        Returns:
            ApprovalRequest（status 为 APPROVED 或 DENIED）
        """
        req = ApprovalRequest(
            id=self._next_id,
            session_id=session_id,
            skill_name=skill_name,
            step_number=step_number,
            risk_score=risk_score,
            risk_reasons=risk_reasons or [],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.timeout_seconds),
        )
        self._requests[req.id] = req
        self._next_id += 1

        logger.info(
            "审批请求已创建: id=%d, session=%d, skill=%s, risk=%d, timeout=%ds",
            req.id, session_id, skill_name, risk_score, self.timeout_seconds,
        )

        # 等待审批结果（或超时）
        try:
            await asyncio.wait_for(
                req.event.wait(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            req.status = ApprovalStatus.TIMEOUT
            req.resolved_at = datetime.now(timezone.utc)
            logger.warning("审批请求超时: id=%d", req.id)

        return req

    # ================================================================
    # 审批操作
    # ================================================================

    async def approve(self, approval_id: int, resolved_by: str = "user") -> ApprovalRequest:
        """
        批准审批请求

        Args:
            approval_id: 审批请求 ID
            resolved_by: 操作人标识

        Returns:
            更新后的 ApprovalRequest

        Raises:
            ValueError: 审批请求不存在或已过期
        """
        req = self._get_request(approval_id)
        req.status = ApprovalStatus.APPROVED
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = resolved_by
        req.event.set()  # ← 唤醒 Agent
        logger.info("审批已批准: id=%d, by=%s", approval_id, resolved_by)
        return req

    async def deny(
        self,
        approval_id: int,
        reason: str = "",
        resolved_by: str = "user",
    ) -> ApprovalRequest:
        """
        拒绝审批请求

        Args:
            approval_id: 审批请求 ID
            reason:      拒绝原因
            resolved_by: 操作人标识
        """
        req = self._get_request(approval_id)
        req.status = ApprovalStatus.DENIED
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = resolved_by
        if reason:
            req.risk_reasons.append(f"拒绝原因: {reason}")
        req.event.set()  # ← 唤醒 Agent
        logger.info("审批已拒绝: id=%d, reason=%s, by=%s", approval_id, reason, resolved_by)
        return req

    # ================================================================
    # 查询
    # ================================================================

    def get_request(self, approval_id: int) -> Optional[ApprovalRequest]:
        return self._requests.get(approval_id)

    def get_pending(self, session_id: Optional[int] = None) -> list[ApprovalRequest]:
        """获取待审批的请求"""
        pending = [
            r for r in self._requests.values()
            if r.status == ApprovalStatus.PENDING
        ]
        if session_id is not None:
            pending = [r for r in pending if r.session_id == session_id]
        return pending

    def get_history(self, session_id: Optional[int] = None) -> list[ApprovalRequest]:
        """获取已处理的审批记录"""
        resolved = [
            r for r in self._requests.values()
            if r.status != ApprovalStatus.PENDING
        ]
        if session_id is not None:
            resolved = [r for r in resolved if r.session_id == session_id]
        return resolved

    def _get_request(self, approval_id: int) -> ApprovalRequest:
        """获取审批请求（不存在或已过期则抛异常）"""
        req = self._requests.get(approval_id)
        if req is None:
            raise ValueError(f"审批请求不存在: {approval_id}")
        if req.is_expired:
            raise ValueError(f"审批请求已过期: {approval_id}")
        return req
