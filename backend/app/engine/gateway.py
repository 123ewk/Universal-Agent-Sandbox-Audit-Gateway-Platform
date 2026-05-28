"""
AuditGateway — 审计网关

这是 ShadowOS 架构中最核心的安全控制点。
所有 Skill 调用**必须**经过 AuditGateway，不允许绕过。

强制流程：
  Skill Call
    → 获取 Skill 实例
    → 风险分析 (RiskEngine)
    → 记录审计日志 (AuditLog)
    → 拦截/审批判断
      → L5 高危: 直接拦截
      → L4: 创建审批记录，返回"等待审批"
      → L1-L3: 放行
    → Skill 执行
    → 更新审计日志结果

设计原则：
  - AuditGateway 是单例，全应用共享
  - 不持有 DB session，由调用方传入（依赖注入）
  - 所有异常在 gateway 内部捕获，返回 SkillResult（不抛异常）

TODO:
  - Phase 6: 通过 WebSocket 推送审批请求到前端
  - Phase 7: 集成 LangGraph 的"暂停/等待审批"节点
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRecord, ApprovalStatus as ApprovalStatusEnum
from app.models.audit_log import AuditLog
from app.skills.base import BaseSkill, SkillContext, SkillResult
from app.skills.registry import registry
from app.engine.risk import RiskEngine, RiskAssessment

logger = logging.getLogger(__name__)


# ====================================================================
# ApprovalRequired — 审批需求信号
# ====================================================================


@dataclass
class ApprovalRequired:
    """
    AuditGateway.invoke() 的特殊返回值：
    表示此 Skill 调用需要人工审批，当前已被暂停

    调用方（LangGraph Agent）应：
      1. 将 approval_record_id 传给审批流
      2. 等待前端用户 Allow / Deny
      3. 根据审批结果决定继续或跳过

    审批完成后，调用 AuditGateway.execute_approved() 继续执行。
    """
    approval_record_id: int
    assessment: RiskAssessment
    audit_log_id: int


# ====================================================================
# AuditGateway — 审计网关单例
# ====================================================================


class AuditGateway:
    """
    审计网关 — Skill 调用的统一入口和拦截点

    调用方不应该直接调用 skill.execute()，而是调用 gateway.invoke()。
    """

    def __init__(self) -> None:
        self._risk_engine = RiskEngine()

    # ---- 核心入口 ----

    async def invoke(
        self,
        skill_name: str,
        params: dict[str, Any],
        context: SkillContext,
        db: AsyncSession,
        bypass_approval: bool = False,
    ) -> SkillResult | ApprovalRequired:
        """
        统一的 Skill 调用入口

        Args:
            skill_name:     Skill 名称（如 "browser_click"）
            params:         调用参数
            context:        执行上下文（session_id, request_id, sandbox_id）
            db:             数据库会话（用于写入审计日志和审批记录）
            bypass_approval: 是否跳过审批（仅用于测试或已审批的重放）

        Returns:
            SkillResult | ApprovalRequired

        调用方可通过 isinstance(result, ApprovalRequired) 判断是否需要审批。
        """
        audit_log = None
        # 使用 datetime.utcnow() 而非 datetime.now(timezone.utc)，
        # 因为数据库中 expires_at/responded_at 列定义为 timezone-naive DateTime
        start_time = datetime.utcnow()

        try:
            # ---- 步骤 1: 获取 Skill ----
            try:
                skill = registry.get_or_raise(skill_name)
            except KeyError as exc:
                return SkillResult.fail(str(exc))

            # ---- 步骤 2: 风险分析 ----
            assessment = self._risk_engine.assess(skill, params)
            logger.info(
                "Skill 调用评估: name=%s, action=%s, score=%d, reasons=%s",
                skill_name, assessment.suggested_action,
                assessment.score, assessment.reasons,
            )

            # ---- 步骤 3: 记录审计日志 ----
            audit_log = AuditLog(
                session_id=context.session_id,
                step_number=params.get("_step_number", 0),
                action_type=skill_name,
                action_input=params,
                is_high_risk=assessment.requires_approval or assessment.is_blocked,
                risk_reason=str(assessment.to_dict()),
                approved=None,  # 尚未审批
                success=None,   # 尚未执行
                execution_time_ms=0,
                action_taken_at=start_time,
            )
            db.add(audit_log)
            await db.flush()  # 获取 audit_log.id
            await db.refresh(audit_log)

            # ---- 步骤 4a: L5 直接拦截 ----
            if assessment.is_blocked:
                audit_log.success = False
                audit_log.error_detail = f"安全拦截: {'; '.join(assessment.reasons)}"
                await db.flush()
                return SkillResult.fail(
                    f"安全策略拦截此操作: {'; '.join(assessment.reasons)}"
                )

            # ---- 步骤 4b: L4 需要审批 ----
            if assessment.requires_approval and not bypass_approval:
                approval = ApprovalRecord(
                    session_id=context.session_id,
                    audit_log_id=audit_log.id,
                    risk_type=skill_name,
                    risk_description=self._build_risk_description(skill, params, assessment),
                    risk_score=assessment.score,
                    action_context={
                        "skill": skill_name,
                        "params": params,
                        "assessment": assessment.to_dict(),
                        "request_id": context.request_id,
                    },
                    status=ApprovalStatusEnum.PENDING,
                    expires_at=datetime.utcnow() + timedelta(minutes=5),
                )
                db.add(approval)
                await db.flush()
                await db.refresh(approval)

                logger.info(
                    "审批已创建: approval_id=%d, skill=%s, score=%d",
                    approval.id, skill_name, assessment.score,
                )

                return ApprovalRequired(
                    approval_record_id=approval.id,
                    assessment=assessment,
                    audit_log_id=audit_log.id,
                )

            # ---- 步骤 5: 执行 Skill（L1-L3 或 bypass_approval） ----
            result = await skill.execute_with_timing(context, **params)

            # ---- 步骤 6: 更新审计日志 ----
            audit_log.success = result.success
            audit_log.action_output = {"data": result.data} if result.data else None
            audit_log.error_detail = result.error
            audit_log.execution_time_ms = result.execution_time_ms
            audit_log.approved = True  # 无需审批的自动通过
            await db.flush()

            return result

        except Exception as exc:
            logger.error("AuditGateway 异常: skill=%s, error=%s", skill_name, exc)
            # 尝试更新审计日志（如果已创建）
            if audit_log is not None and audit_log.id:
                try:
                    audit_log.success = False
                    audit_log.error_detail = f"Gateway 异常: {exc}"
                    await db.flush()
                except Exception:
                    pass
            return SkillResult.fail(f"审计网关异常: {exc}")

    # ---- 审批完成后继续执行 ----

    async def execute_approved(
        self,
        approval_record_id: int,
        context: SkillContext,
        db: AsyncSession,
    ) -> SkillResult:
        """
        审批通过后继续执行被暂停的 Skill 调用

        调用方在收到 ApprovalRequired 后，等待用户审批，
        审批通过后调用此方法执行实际的 Skill 逻辑。

        Args:
            approval_record_id: ApprovalRequired 中返回的审批记录 ID
            context:            执行上下文
            db:                 数据库会话

        Returns:
            SkillResult: 审批后的执行结果
        """
        try:
            # 查询审批记录
            result = await db.execute(
                select(ApprovalRecord).where(ApprovalRecord.id == approval_record_id)
            )
            approval = result.scalar_one_or_none()
            if approval is None:
                return SkillResult.fail(f"审批记录不存在: id={approval_record_id}")

            # 检查审批状态
            if approval.status != ApprovalStatusEnum.APPROVED:
                return SkillResult.fail(
                    f"审批尚未通过或已过期: status={approval.status.value}"
                )

            # 获取关联的审计日志
            if approval.audit_log_id is None:
                return SkillResult.fail("审批记录未关联审计日志")

            audit_result = await db.execute(
                select(AuditLog).where(AuditLog.id == approval.audit_log_id)
            )
            audit_log = audit_result.scalar_one_or_none()
            if audit_log is None:
                return SkillResult.fail("关联的审计日志不存在")

            # 获取 Skill 并执行
            skill = registry.get_or_raise(approval.risk_type)
            params = audit_log.action_input or {}

            # 执行时使用 bypass_approval=True 不再触发审批
            return await self.invoke(
                skill_name=approval.risk_type,
                params=params,
                context=context,
                db=db,
                bypass_approval=True,
            )

        except Exception as exc:
            logger.error("审批后执行失败: approval_id=%d, error=%s", approval_record_id, exc)
            return SkillResult.fail(f"审批后执行异常: {exc}")

    # ---- 内部方法 ----

    def _build_risk_description(
        self,
        skill: BaseSkill,
        params: dict[str, Any],
        assessment: RiskAssessment,
    ) -> str:
        """构建面向人类审批者的危险操作描述"""
        parts = [
            f"技能: {skill.name} ({skill.description})",
            f"风险等级: L{assessment.level.value} (评分: {assessment.score})",
        ]
        if assessment.reasons:
            parts.append(f"风险原因: {'; '.join(assessment.reasons)}")
        # 添加关键参数摘要
        safe_params = {k: v for k, v in params.items() if not k.startswith("_")}
        if safe_params:
            param_summary = ", ".join(f"{k}={v}" for k, v in list(safe_params.items())[:5])
            parts.append(f"参数: {param_summary}")
        return " | ".join(parts)


# ---- 全局单例 ----

gateway = AuditGateway()
