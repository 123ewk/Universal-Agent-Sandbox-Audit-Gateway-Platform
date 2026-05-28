"""
Skill 运行时核心抽象层

架构设计：
  BaseSkill 是 Agent 所有能力的抽象基类。
  每个 Skill 必须声明自己的名称、描述、分类和风险等级。
  所有 Skill 通过 SkillRegistry 统一注册，由 AuditGateway 统一调度。

  关键设计原则 — "显式安全"：
    每个 Skill 的 risk_level 在定义时已经确定，执行时 RiskEngine 再
    根据具体参数进行动态评估（如 read_file 是 L3，但读取 /etc/passwd 升 L4）。
    这保证了安全策略的双层检查：静态声明 + 动态分析。
"""
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.skills.enums import SkillCategory, SkillTier  # noqa: F401 — 重导出供外部使用

logger = logging.getLogger(__name__)


# ====================================================================
# 数据类：Skill 执行上下文与结果
# ====================================================================


@dataclass
class SkillContext:
    """
    Skill 执行上下文

    在 Agent 每步执行时创建，包含当前会话、请求和沙箱的唯一标识。
    使用 dataclass 而非 Pydantic（性能更优、无验证开销、适合高频创建）。
    """
    session_id: int = 0
    request_id: str = ""
    sandbox_id: str | None = None
    sandbox_engine: Any | None = None  # Phase 6: SandboxEngine 实例，Skills 通过它操作浏览器
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """
    Skill 执行结果

    三个核心字段：
      success:       执行是否成功（业务成功 / 异常失败）
      data:          成功时的返回数据（dict / list / str 均可）
      error:         失败时的错误消息
      execution_time_ms: 执行耗时（毫秒），由 BaseSkill.execute() 自动计时
    """
    success: bool = True
    data: Any = None
    error: str | None = None
    execution_time_ms: int = 0

    @classmethod
    def ok(cls, data: Any = None) -> "SkillResult":
        """快捷构造：成功结果"""
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str, data: Any = None) -> "SkillResult":
        """快捷构造：失败结果"""
        return cls(success=False, data=data, error=error)


# ====================================================================
# 抽象基类：BaseSkill
# ====================================================================


class BaseSkill(ABC):
    """
    所有 Skill 的抽象基类

    子类必须定义：
      name:         技能唯一名称（如 "browser_click"），用于 Agent 调用时的标识
      description:  面向 LLM 的自然语言描述，用于 LLM 判断何时调用此 Skill
      category:     技能分类（browser / file / shell / api / database / system）
      risk_level:   静态声明的风险等级（L1-L5），在类定义时固定

    子类必须实现：
      execute():    核心业务逻辑

    使用方式：
      class ClickSkill(BaseSkill):
          name = "browser_click"
          description = "点击页面上的元素"
          category = SkillCategory.BROWSER
          risk_level = RiskLevel.L2_INTERACTION

          async def execute(self, context: SkillContext, **params) -> SkillResult:
              # 实现点击逻辑
              ...
    """

    name: str = ""
    description: str = ""
    category: SkillCategory = SkillCategory.BROWSER
    tier: SkillTier = SkillTier.CORE  # 渐进式披露层级，默认 CORE 始终可见
    risk_level: Any = None  # RiskLevel 类型，推迟导入避免循环依赖

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Python 元编程：子类继承时自动验证必填字段

        原理：
          __init_subclass__ 在每个子类被创建时自动调用，
          用于在类定义阶段（而非实例化阶段）验证子类是否正确定义了 name/description。
        """
        super().__init_subclass__(**kwargs)
        if not cls.name or not cls.description:
            raise TypeError(
                f"Skill 子类 {cls.__name__} 必须定义 name 和 description 类属性"
            )

    @abstractmethod
    async def execute(self, context: SkillContext, **params: Any) -> SkillResult:
        """
        执行技能的核心逻辑

        Args:
            context: 执行上下文（session_id, request_id, sandbox_id）
            **params: 技能参数（由具体 Skill 定义其 schema）

        Returns:
            SkillResult: 执行结果
        """

    # ---- 自动计时封装 ----

    async def execute_with_timing(
        self, context: SkillContext, **params: Any
    ) -> SkillResult:
        """
        自动记录执行耗时的 execute 封装

        AuditGateway 调用此方法而非直接调用 execute()，
        确保每条 Skill 执行都有计时（用于审计和性能监控）。
        """
        start = time.monotonic()
        try:
            result = await self.execute(context, **params)
            elapsed = int((time.monotonic() - start) * 1000)
            result.execution_time_ms = elapsed
            return result
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error(
                "Skill 执行异常: name=%s, elapsed_ms=%d, error=%s",
                self.name, elapsed, exc,
            )
            return SkillResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                execution_time_ms=elapsed,
            )
