"""
SkillRegistry — 技能注册中心

功能：
  1. 全局注册所有 Skill 实例，提供名称到实例的映射
  2. 支持按风险等级、分类查询技能列表
  3. 使用 Singleton 模式确保全应用共享同一个注册表
     （避免 Agent 在不同请求中看到不同的 Skill 集合）

设计动机：
  SkillRegistry 是 Skill 系统的"黄页中心"：
  - AuditGateway 通过 registry.get("browser_click") 获取 Skill 实例并执行
  - RiskEngine 通过 registry.list_by_risk(L4) 获取所有高危技能
  - FastAPI 启动时调用 registry.discover() 自动注册所有 Skills
"""
import logging
from typing import Any

from app.skills.base import BaseSkill
from app.skills.enums import RiskLevel, SkillCategory, SkillTier

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Skill 注册表（全局单例）

    使用方式：
      registry = SkillRegistry()
      registry.register(click_skill)
      skill = registry.get("browser_click")
      skills = registry.list_by_risk(RiskLevel.L4)
    """

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    # ---- 注册与获取 ----

    def register(self, skill: BaseSkill) -> None:
        """
        注册一个 Skill 实例

        Raises:
            ValueError: 如果 skill.name 已存在
        """
        if not skill.name:
            raise ValueError(f"Skill 必须有 name 属性: {type(skill).__name__}")
        if skill.name in self._skills:
            raise ValueError(f"Skill 名称冲突，已存在同名 Skill: {skill.name}")
        self._skills[skill.name] = skill
        logger.info(
            "Skill 已注册: name=%s, category=%s, risk_level=%s",
            skill.name, skill.category.value if hasattr(skill.category, 'value') else skill.category,
            skill.risk_level.value if hasattr(skill.risk_level, 'value') else skill.risk_level,
        )

    def get(self, name: str) -> BaseSkill | None:
        """根据名称获取 Skill 实例，不存在时返回 None"""
        return self._skills.get(name)

    def get_or_raise(self, name: str) -> BaseSkill:
        """
        根据名称获取 Skill 实例，不存在时抛出 KeyError

        用于 AuditGateway — 调用方明确期望 Skill 必须存在
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"未找到名为 '{name}' 的 Skill")
        return skill

    # ---- 查询与过滤 ----

    def list_all(self) -> list[BaseSkill]:
        """返回所有已注册的 Skill"""
        return list(self._skills.values())

    def list_by_risk(self, level: RiskLevel) -> list[BaseSkill]:
        """按风险等级筛选"""
        return [s for s in self._skills.values() if s.risk_level == level]

    def list_by_category(self, category: SkillCategory) -> list[BaseSkill]:
        """按分类筛选"""
        return [s for s in self._skills.values() if s.category == category]

    def list_by_tier(self, tier: SkillTier) -> list[BaseSkill]:
        """按披露层级筛选"""
        return [s for s in self._skills.values() if s.tier == tier]

    def list_by_tiers(self, tiers: set[SkillTier]) -> list[BaseSkill]:
        """按多个披露层级筛选"""
        return [s for s in self._skills.values() if s.tier in tiers]

    def count(self) -> int:
        """已注册 Skill 总数"""
        return len(self._skills)

    # ---- 自动发现 ----

    def discover(self) -> int:
        """
        自动扫描并注册所有 Skill 子类实例

        原理：
          通过 BaseSkill.__subclasses__() 获取所有直接子类，
          实例化后注册到 registry。

        注意：
          __subclasses__() 只返回直接子类，不递归。
          但我们的 Skill 体系只有一层继承（BaseSkill → 具体 Skill），
          所以这个限制不影响。
        """
        from app.skills.base import BaseSkill as SkillBase

        count = 0
        for skill_cls in SkillBase.__subclasses__():
            # 跳过抽象子类（如果某个中间类还标记为抽象）
            if getattr(skill_cls, "__abstract__", False):
                continue
            # 跳过测试模块中定义的临时类（如 NoNameSkill）
            # __subclasses__() 不会自动移除模块级失败创建的类
            if skill_cls.__module__ and skill_cls.__module__.startswith("test"):
                continue
            try:
                instance = skill_cls()
                self.register(instance)
                count += 1
            except TypeError:
                continue
            except Exception as exc:
                logger.warning(
                    "Skill 自动注册失败: class=%s, error=%s",
                    skill_cls.__name__, exc,
                )
        logger.info("Skill 自动发现完成: 共注册 %d 个", count)
        return count


# ---- 全局单例 ----

# 应用启动时先导入所有 Skill 模块，再调用 registry.discover()
# 由 engine.__init__ 中的 init_skills() 统一管理
registry = SkillRegistry()
