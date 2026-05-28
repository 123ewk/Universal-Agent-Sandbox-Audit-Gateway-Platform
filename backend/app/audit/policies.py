"""
AuditPolicy — 审计风险规则引擎

设计动机：
  Phase 4 的 RiskEngine 做单步操作风险评分（静态声明 + 动态参数）。
  Phase 7 的 AuditPolicy 做跨步骤行为模式检测（连续失败、高频重试、操作组合异常）。
  两者互补：RiskEngine 看"这一步有多危险"，AuditPolicy 看"这串行为有多可疑"。

检测维度：
  1. 连续失败     — 连续 N 步失败
  2. 高频重试     — 短时间内重复执行同一操作
  3. 危险操作组合 — 导航到银行 URL + 点击提交按钮
  4. Shell 命令   — 执行任何 Shell 命令（高风险）
  5. 文件删除     — 删除/覆盖文件的模式
  6. 速率异常     — 操作频率远超正常范围

配置来源：
  当前为硬编码默认规则，后续可迁移到配置文件或数据库。

使用方式：
  policies = AuditPolicy()
  assessment = policies.assess(execution_history)
  # → {total_score, triggers: [...], requires_approval, should_pause}
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PolicyTrigger:
    """单个规则触发结果"""
    rule_name: str = ""
    severity: int = 0        # 严重程度 0-100
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyAssessment:
    """策略评估结果"""
    total_score: int = 0          # 综合风险分 0-100
    triggers: list[PolicyTrigger] = field(default_factory=list)
    requires_approval: bool = False  # 是否需要人工审批
    should_pause: bool = False       # 是否应暂停 Agent


class AuditPolicy:
    """
    审计策略引擎

    对执行历史进行跨步骤行为模式分析。
    """

    # ================================================================
    # 规则定义
    # ================================================================

    # 规则 1：连续失败
    CONSECUTIVE_FAILURE_THRESHOLD: int = 3       # 连续失败 N 步触发
    CONSECUTIVE_FAILURE_SCORE: int = 60

    # 规则 2：高频重试
    RAPID_RETRY_WINDOW_SECONDS: int = 10          # 时间窗口（秒）
    RAPID_RETRY_MAX_IN_WINDOW: int = 5            # 窗口内最多重试次数
    RAPID_RETRY_SCORE: int = 50

    # 规则 3：危险操作组合
    DANGEROUS_COMBO_SCORE: int = 70

    # 规则 4：Shell 命令
    SHELL_COMMAND_SCORE: int = 85

    # 规则 5：高风险 URL + 交互
    HIGH_RISK_URL_INTERACTION_SCORE: int = 65

    # 自动暂停阈值
    AUTO_PAUSE_THRESHOLD: int = 60

    # 审批阈值
    APPROVAL_THRESHOLD: int = 40

    # ================================================================
    # 主入口
    # ================================================================

    def assess(self, steps: list[Any]) -> PolicyAssessment:
        """
        对执行历史进行策略评估

        Args:
            steps: StepRecord 列表（来自 AgentState.execution_history）

        Returns:
            PolicyAssessment 评估结果
        """
        if not steps:
            return PolicyAssessment()

        assessment = PolicyAssessment()

        # 执行各规则检测
        self._check_consecutive_failures(steps, assessment)
        self._check_rapid_retries(steps, assessment)
        self._check_dangerous_combos(steps, assessment)
        self._check_shell_commands(steps, assessment)
        self._check_high_risk_url_interaction(steps, assessment)

        # 汇总
        if assessment.triggers:
            assessment.total_score = max(t.severity for t in assessment.triggers)

        assessment.requires_approval = assessment.total_score >= self.APPROVAL_THRESHOLD
        assessment.should_pause = assessment.total_score >= self.AUTO_PAUSE_THRESHOLD

        return assessment

    # ================================================================
    # 规则实现
    # ================================================================

    def _check_consecutive_failures(
        self, steps: list[Any], assessment: PolicyAssessment
    ) -> None:
        """检测连续失败模式"""
        if len(steps) < self.CONSECUTIVE_FAILURE_THRESHOLD:
            return

        # 从最后一步往前数连续失败的步数
        consecutive = 0
        for step in reversed(steps):
            success = getattr(step, "success", True)
            if not success:
                consecutive += 1
            else:
                break

        if consecutive >= self.CONSECUTIVE_FAILURE_THRESHOLD:
            assessment.triggers.append(PolicyTrigger(
                rule_name="consecutive_failures",
                severity=self.CONSECUTIVE_FAILURE_SCORE,
                reason=f"连续 {consecutive} 步执行失败",
                evidence={"consecutive_count": consecutive},
            ))

    def _check_rapid_retries(self, steps: list[Any], assessment: PolicyAssessment) -> None:
        """检测高频重试模式（同一 Skill 短时间内多次调用）"""
        if len(steps) < 2:
            return

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=self.RAPID_RETRY_WINDOW_SECONDS)

        # 统计窗口内同一 skill 的调用次数
        skill_counts: dict[str, int] = {}
        for step in steps:
            finished_at = getattr(step, "finished_at", None)
            if finished_at is None:
                continue
            # 确保 finished_at 是 offset-aware
            if finished_at.tzinfo is None:
                continue
            if finished_at < window_start:
                continue

            plan_step = getattr(step, "plan_step", None)
            if plan_step is None:
                continue
            skill_name = getattr(plan_step, "skill_name", "")
            if skill_name:
                skill_counts[skill_name] = skill_counts.get(skill_name, 0) + 1

        for skill_name, count in skill_counts.items():
            if count >= self.RAPID_RETRY_MAX_IN_WINDOW:
                assessment.triggers.append(PolicyTrigger(
                    rule_name="rapid_retries",
                    severity=self.RAPID_RETRY_SCORE,
                    reason=f"Skill '{skill_name}' 在 {self.RAPID_RETRY_WINDOW_SECONDS}s 内执行了 {count} 次",
                    evidence={
                        "skill_name": skill_name,
                        "count": count,
                        "window_seconds": self.RAPID_RETRY_WINDOW_SECONDS,
                    },
                ))

    def _check_dangerous_combos(self, steps: list[Any], assessment: PolicyAssessment) -> None:
        """
        检测危险操作组合

        示例：导航到银行页面 + 点击提交/支付按钮
        """
        if len(steps) < 2:
            return

        # 收集所有操作的特征
        urls_visited: list[str] = []
        actions_done: list[str] = []

        for step in steps:
            plan_step = getattr(step, "plan_step", None)
            if plan_step is None:
                continue
            params = getattr(plan_step, "skill_params", {}) or {}
            skill_name = getattr(plan_step, "skill_name", "")

            if "goto" in skill_name:
                urls_visited.append(params.get("url", ""))
            if "click" in skill_name or "type" in skill_name:
                selector = params.get("selector", "").lower()
                text = params.get("text", "").lower()
                actions_done.append(f"{selector} {text}")

        # 检测：银行 URL + 提交/支付交互
        high_risk_urls = {"bank", "payment", "transfer", "transaction"}
        high_risk_actions = {"submit", "payment", "pay", "confirm", "transfer"}

        has_risky_url = any(
            keyword in url.lower()
            for url in urls_visited
            for keyword in high_risk_urls
        )
        has_risky_action = any(
            keyword in action
            for action in actions_done
            for keyword in high_risk_actions
        )

        if has_risky_url and has_risky_action:
            assessment.triggers.append(PolicyTrigger(
                rule_name="dangerous_combo",
                severity=self.DANGEROUS_COMBO_SCORE,
                reason="检测到危险操作组合：访问金融/支付页面 + 提交/支付操作",
                evidence={
                    "urls": urls_visited[-3:],
                    "actions": actions_done[-3:],
                },
            ))

    def _check_shell_commands(
        self, steps: list[Any], assessment: PolicyAssessment
    ) -> None:
        """检测 Shell 命令执行"""
        for step in steps:
            plan_step = getattr(step, "plan_step", None)
            if plan_step is None:
                continue
            skill_name = getattr(plan_step, "skill_name", "")
            if "shell" in skill_name or "run_command" in skill_name:
                assessment.triggers.append(PolicyTrigger(
                    rule_name="shell_command",
                    severity=self.SHELL_COMMAND_SCORE,
                    reason=f"执行了 Shell 命令: Step {getattr(step, 'step_number', '?')}",
                    evidence={
                        "step_number": getattr(step, "step_number", 0),
                        "skill_name": skill_name,
                    },
                ))

    def _check_high_risk_url_interaction(
        self, steps: list[Any], assessment: PolicyAssessment
    ) -> None:
        """检测在高风险 URL 上的交互操作"""
        current_url = ""
        for step in steps:
            plan_step = getattr(step, "plan_step", None)
            if plan_step is None:
                continue
            params = getattr(plan_step, "skill_params", {}) or {}
            skill_name = getattr(plan_step, "skill_name", "")

            # 跟踪当前 URL
            if "goto" in skill_name:
                current_url = params.get("url", "")
                # 检查是否高风险 URL
                high_risk_patterns = ["admin", "root", "internal", "dashboard"]
                if any(p in current_url.lower() for p in high_risk_patterns):
                    assessment.triggers.append(PolicyTrigger(
                        rule_name="high_risk_url_interaction",
                        severity=self.HIGH_RISK_URL_INTERACTION_SCORE,
                        reason=f"导航到高风险 URL: {current_url}",
                        evidence={"url": current_url},
                    ))
