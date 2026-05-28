"""
RiskEngine — 风险分析引擎

职责：
  对每个 Skill 调用进行双层风险评估：
    1. 静态分析：Skill 声明的 risk_level（类级别）
    2. 动态分析：根据具体参数、目标 URL、命令内容等（调用级别）

  L1-L5 与 risk_score(0-100) 的映射：
    等级    范围       含义
    L1      1-20      只读操作，安全
    L2     21-40      普通交互，需审计
    L3     41-60      文件操作，需确认
    L4     61-80      Shell 执行，需人工审批
    L5     81-100     高危破坏，需审批 + 隔离

使用方式：
  engine = RiskEngine()
  assessment = engine.assess(skill, {"url": "https://bank.com/transfer"})
  # → RiskAssessment(level=L5, score=85, requires_approval=True, ...)
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from app.skills.base import BaseSkill
from app.skills.enums import RiskLevel
from app.config import settings

logger = logging.getLogger(__name__)


# ====================================================================
# RiskAssessment — 风险评估结果
# ====================================================================


@dataclass
class RiskAssessment:
    """
    风险评估结果

    属性：
      level:            最终风险等级（L1-L5）
      score:            风险评分 (0-100)
      reasons:          触发该评分的所有原因列表
      requires_approval: 是否需要人类审批
      is_blocked:        是否应被直接拦截（无需审批）
      suggested_action:  建议动作: "allow" / "warn" / "require_approval" / "block"
    """
    level: RiskLevel
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = False
    is_blocked: bool = False
    suggested_action: str = "allow"

    def to_dict(self) -> dict[str, Any]:
        """转为字典，用于序列化到 AuditLog 的 risk_reason 字段"""
        return {
            "level": self.level.value if hasattr(self.level, 'value') else self.level,
            "score": self.score,
            "reasons": self.reasons,
            "requires_approval": self.requires_approval,
            "is_blocked": self.is_blocked,
            "suggested_action": self.suggested_action,
        }


# ====================================================================
# RiskEngine
# ====================================================================


class RiskEngine:
    """
    风险分析引擎

    评估流程：
      1. 基础分 = skill.risk_level × 20
      2. 根据参数内容进行"调优"（up-score 或 down-score）
      3. 特殊场景"一键否决"（如银行 URL、删除命令）
      4. 根据最终 score 确定等级、建议动作

    为什么要做动态参数分析？
      一个 L3 的 file_read skill 读取普通文件 vs 读取 /etc/passwd，
      风险完全不同。静态等级只是"基分"，参数决定最终分。
    """

    _risk_keywords: dict[str, int] = {
        # 金融敏感
        "bank": 30, "payment": 25, "transfer": 25, "transaction": 20,
        "login": 15, "password": 20, "token": 10,
        # 系统敏感
        "/etc/": 20, "/proc/": 15, "/sys/": 15,
        "sudo": 25, "su ": 25, "chmod": 15, "chown": 20,
        # 删除/破坏
        "delete": 25, "drop ": 30, "truncate": 25,
        "rm ": 15, "rm -rf": 30,
    }

    def assess(
        self,
        skill: BaseSkill,
        params: dict[str, Any] | None = None,
    ) -> RiskAssessment:
        """
        对 Skill 调用进行风险评估

        Args:
            skill:  被调用的 Skill 实例
            params: 调用的具体参数（用于动态分析）

        Returns:
            RiskAssessment: 包含评分、等级和决策建议
        """
        params = params or {}
        reasons: list[str] = []
        extra_score = 0

        # ---- 步骤 1: 基础分 ----
        base_score = skill.risk_level.value * 20
        level = skill.risk_level

        # ---- 步骤 2: 动态参数分析 ----
        param_str = self._flatten_params(params)
        for keyword, score in self._risk_keywords.items():
            if keyword.lower() in param_str.lower():
                extra_score += score
                reasons.append(f"参数包含敏感关键词: {keyword} (+{score})")

        # ---- 步骤 3: 高危 URL 检查 ----
        url = params.get("url", "")
        if url:
            for domain in settings.HIGH_RISK_DOMAINS:
                if domain in url.lower():
                    extra_score += 25
                    reasons.append(f"访问高危域名: {domain} (+25)")

        # ---- 步骤 4: URL 黑名单检查 ----
        if url:
            for block_pattern in settings.URL_BLOCKLIST:
                block_glob = block_pattern.replace("://*", "://")
                if block_glob in url:
                    return RiskAssessment(
                        level=RiskLevel.L5_DESTRUCTIVE,
                        score=100,
                        reasons=[f"跳过黑名单 URL: {url}"],
                        requires_approval=False,
                        is_blocked=True,
                        suggested_action="block",
                    )

        # ---- 步骤 5: 计算最终评分 ----
        final_score = min(base_score + extra_score, 100)

        # 根据最终评分确定等级（可能升级）
        level = self._score_to_level(final_score)

        # ---- 步骤 6: 决策 ----
        requires_approval = level >= RiskLevel.L4_SHELL
        is_blocked = level == RiskLevel.L5_DESTRUCTIVE and extra_score >= 40

        if is_blocked:
            suggested_action = "block"
        elif requires_approval:
            suggested_action = "require_approval"
        elif level >= RiskLevel.L3_FILE_OP:
            suggested_action = "warn"
        else:
            suggested_action = "allow"

        return RiskAssessment(
            level=level,
            score=final_score,
            reasons=reasons,
            requires_approval=requires_approval,
            is_blocked=is_blocked,
            suggested_action=suggested_action,
        )

    # ---- 辅助方法 ----

    def _score_to_level(self, score: int) -> RiskLevel:
        """将 risk_score(0-100) 转换为 RiskLevel"""
        if score <= 20:
            return RiskLevel.L1_READONLY
        elif score <= 40:
            return RiskLevel.L2_INTERACTION
        elif score <= 60:
            return RiskLevel.L3_FILE_OP
        elif score <= 80:
            return RiskLevel.L4_SHELL
        else:
            return RiskLevel.L5_DESTRUCTIVE

    @staticmethod
    def _flatten_params(params: dict[str, Any], depth: int = 0) -> str:
        """
        将嵌套 params 展开为纯字符串，用于关键词匹配

        避免因为嵌套结构（如 {"path": "/etc/passwd"}）漏检关键词。

        Args:
            params: 可能嵌套的参数 dict
            depth:  递归深度，防止无限递归（最大 3 层）
        """
        if depth > 3:
            return ""
        parts: list[str] = []
        for key, value in params.items():
            parts.append(str(key))
            if isinstance(value, dict):
                parts.append(self._flatten_params(value, depth + 1))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, (str, int, float)):
                        parts.append(str(item))
            else:
                parts.append(str(value))
        return " ".join(parts)
