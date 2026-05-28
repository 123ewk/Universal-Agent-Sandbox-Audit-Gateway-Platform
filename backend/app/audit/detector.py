"""
BehaviorDetector — 跨步骤行为检测器

设计动机：
  Phase 4 的 RiskEngine 做单步风险评分（静态 + 动态参数）。
  Phase 7 的 BehaviorDetector 分析执行历史的整体行为模式：
    连续失败 → 可能陷入循环
    高频重试 → 可能被反爬/封禁
    危险组合 → 金融/支付操作链
    Shell 执行 → 最高风险

  将 AuditPolicy（规则定义）与 AgentState（执行数据）连接起来。

架构位置：
  AgentGraph._reflect_node 中调用 detector.analyze(state)，
  如果 should_pause → 设置 WAITING_APPROVAL 状态 + 推送 WS 事件。

使用方式：
  detector = BehaviorDetector(policies=AuditPolicy())
  assessment = detector.analyze(state, connection_manager)
  # → PolicyAssessment {total_score, triggers, should_pause, requires_approval}
"""
import logging
from typing import Optional

from app.agent.state import AgentState
from app.audit.policies import AuditPolicy, PolicyAssessment
from app.ws.manager import ConnectionManager
from app.ws.protocol import (
    RiskPayload,
    audit_risk_detected,
)

logger = logging.getLogger(__name__)


class BehaviorDetector:
    """
    跨步骤行为检测器

    在 Agent 的 Reflect 阶段运行，分析执行历史的整体模式。
    检测到风险时通过 WS 推送到前端。
    """

    def __init__(self, policies: Optional[AuditPolicy] = None) -> None:
        self.policies = policies or AuditPolicy()
        self._alert_history: list[PolicyAssessment] = []  # 保留最近的评估记录

    # ================================================================
    # 分析入口
    # ================================================================

    async def analyze(
        self,
        state: AgentState,
        ws_manager: Optional[ConnectionManager] = None,
    ) -> PolicyAssessment:
        """
        分析当前执行历史，返回策略评估结果

        Args:
            state:      当前 AgentState
            ws_manager: WebSocket 连接管理器（用于推送风险告警）

        Returns:
            PolicyAssessment
        """
        assessment = self.policies.assess(state.execution_history)
        self._alert_history.append(assessment)

        if assessment.triggers:
            logger.warning(
                "行为检测触发: session=%d, score=%d, triggers=%d, pause=%s",
                state.session_id,
                assessment.total_score,
                len(assessment.triggers),
                assessment.should_pause,
            )

            # 通过 WebSocket 推送风险告警
            if ws_manager and state.session_id:
                for trigger in assessment.triggers:
                    await ws_manager.broadcast(
                        state.session_id,
                        audit_risk_detected(
                            state.session_id,
                            RiskPayload(
                                risk_score=trigger.severity,
                                risk_level=self._score_to_level(trigger.severity),
                                reasons=[trigger.reason],
                                requires_approval=assessment.requires_approval,
                            ),
                        ),
                    )

        return assessment

    # ================================================================
    # 历史查询
    # ================================================================

    def get_recent_alerts(self, limit: int = 5) -> list[PolicyAssessment]:
        """获取最近的告警记录"""
        return self._alert_history[-limit:]

    def get_session_risk_trend(self) -> list[int]:
        """获取风险分趋势（最近 10 次评估）"""
        return [a.total_score for a in self._alert_history[-10:]]

    def clear_history(self) -> None:
        """清除告警历史（Session 结束后）"""
        self._alert_history.clear()

    # ================================================================
    # 辅助
    # ================================================================

    @staticmethod
    def _score_to_level(score: int) -> int:
        """风险分 (0-100) → 风险等级 (1-5)"""
        if score <= 20:
            return 1
        elif score <= 40:
            return 2
        elif score <= 60:
            return 3
        elif score <= 80:
            return 4
        else:
            return 5
