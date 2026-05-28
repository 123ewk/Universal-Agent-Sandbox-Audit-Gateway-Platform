"""
SkillSelector — 渐进式技能选择器

设计动机：
  一次性给 LLM 注册 8 个 Tool 是浪费 Token 且不安全的。
  一个只做"导航+截图"的 Agent 不需要看到 run_command。
  SkillSelector 根据 Agent 执行阶段，动态决定哪些 Skills 对 LLM 可见。

核心流程：
  1. Agent Planner 分析用户任务 → 生成执行计划
  2. SkillSelector 根据计划推断需要解锁的 Tier
  3. (可选) Shell Tier 需要人工授权
  4. LLM 只看到当前 Tier 及以下的所有 Skills
  5. 执行完当前步骤后回到步骤 2

使用方式：
  selector = SkillSelector()
  selector.unlock(SkillTier.INTERACTION)          # Agent 决定解锁交互技能
  tools = selector.get_llm_tools()                 # 返回当前可见的 Tool 列表（OpenAI 格式）
  skill = selector.get_skill("browser_click")      # 获取 Skill 实例（走 AuditGateway）
  selector.lock()                                   # 重置到初始状态

与 AuditGateway 的关系：
  SkillSelector 负责"选"，AuditGateway 负责"执行"。
  Selector 决定 LLM 能看到什么，Gateway 决定能不能执行。
  两层互不依赖，保障安全。
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.skills.base import BaseSkill
from app.skills.enums import SkillTier
from app.skills.registry import registry

logger = logging.getLogger(__name__)

# skill.md 文档目录（与此文件同级的 descriptions/ 目录）
_SKILL_DOC_DIR = os.path.join(os.path.dirname(__file__), "descriptions")


# ====================================================================
# Tier 关键词提示表
# ====================================================================

# Agent Planner 分析任务描述或计划步骤时，
# 通过这些关键词推断需要解锁哪个 Tier
TIER_KEYWORDS: dict[SkillTier, list[str]] = {
    SkillTier.CORE: [
        "goto", "navigate", "导航", "打开", "访问", "搜索",
        "screenshot", "截图", "查看",
        "extract", "提取", "获取内容", "read page", "获取文本",
    ],
    SkillTier.INTERACTION: [
        "click", "点击", "选择", "选中",
        "type", "输入", "填写", "提交", "搜索框",
        "scroll", "滚动", "hover", "悬停",
    ],
    SkillTier.FILE: [
        "read_file", "读文件", "读取", "打开文件",
        "write_file", "写文件", "写入", "保存", "下载", "上传",
        "file", "文件", "create file", "创建文件",
    ],
    SkillTier.SHELL: [
        "run_command", "执行命令", "shell", "terminal", "终端",
        "部署", "install", "git", "npm", "docker",
        "run", "执行", "命令", "编译",
    ],
}

# 每个 Tier 的解锁门槛描述（用于 Agent 确认）
TIER_DESCRIPTIONS: dict[SkillTier, str] = {
    SkillTier.CORE: "基础只读操作（导航、截图、提取文本）",
    SkillTier.INTERACTION: "浏览器交互操作（点击、输入）",
    SkillTier.FILE: "文件系统操作（读取、写入文件）",
    SkillTier.SHELL: "Shell 命令执行（需人工审批）",
}


# ====================================================================
# SkillSelector
# ====================================================================


class SkillSelector:
    """
    渐进式技能选择器

    维护一个"当前可见的 Tier 集合"，
    所有查询和返回到 LLM 的技能都受此集合限制。
    使用 SkillSelector 替代直接使用 registry 作为 LLM 的 tool 来源。

    线程安全：
      不涉及共享状态，每个 Agent Session 拥有自己的 Selector 实例。
    """

    def __init__(self, initial_tiers: set[SkillTier] | None = None) -> None:
        # 初始只暴露 CORE
        self._active_tiers: set[SkillTier] = (
            initial_tiers or {SkillTier.CORE}
        )

    # ---- Tier 管理 ----

    @property
    def active_tiers(self) -> set[SkillTier]:
        """当前活跃的 Tier 集合（只读快照）"""
        return set(self._active_tiers)

    def unlock(self, tier: SkillTier) -> bool:
        """
        解锁一个 Tier

        Args:
            tier: 要解锁的 Tier

        Returns:
            True 表示该 Tier 是新解锁的，False 表示已经解锁过
        """
        if tier in self._active_tiers:
            return False
        self._active_tiers.add(tier)
        logger.info(
            "Tier 已解锁: %s, 当前可见 Skills: %s",
            tier.value, [s.name for s in self.get_visible_skills()],
        )
        return True

    def lock(self, tier: SkillTier | None = None) -> None:
        """
        锁定一个 Tier

        Args:
            tier: 要锁定的 Tier，None 表示重置到初始状态（仅 CORE）
        """
        if tier is None:
            self._active_tiers = {SkillTier.CORE}
        else:
            self._active_tiers.discard(tier)
        logger.info("Tier 已锁定: %s", tier.value if tier else "ALL (reset)")

    def is_unlocked(self, tier: SkillTier) -> bool:
        """检查某个 Tier 是否已解锁"""
        return tier in self._active_tiers

    # ---- 技能过滤 ----

    def get_visible_skills(self) -> list[BaseSkill]:
        """
        获取当前可见的所有 Skill 实例

        由当前活跃 Tier 集合 + registry 共同决定。
        如果某个 Skill 的 tier 在当前活跃集合中，则可见。
        """
        return registry.list_by_tiers(self._active_tiers)

    def get_skill(self, name: str) -> BaseSkill | None:
        """
        获取指定的 Skill（仅当在该 Skill 的 Tier 已解锁时可用）

        这是由 SkillSelector 判断"能不能看到这个 Skill"，
        而非"能不能执行"（能不能执行由 AuditGateway 判断）。
        """
        skill = registry.get(name)
        if skill is None:
            return None
        if skill.tier not in self._active_tiers:
            logger.warning(
                "Skill '%s' 的 Tier '%s' 尚未解锁",
                name, skill.tier.value,
            )
            return None
        return skill

    def get_visible_names(self) -> list[str]:
        """获取当前可见的所有 Skill 名称"""
        return [s.name for s in self.get_visible_skills()]

    # ---- LLM Tool 格式生成 ----

    def get_llm_tools(self) -> list[dict[str, Any]]:
        """
        生成 OpenAI Function Calling 格式的工具列表

        返回的 dict 可以直接传入 LLM 的 tools 参数。
        格式示例：
          {
              "type": "function",
              "function": {
                  "name": "browser_goto",
                  "description": "导航到指定的 URL 地址",
                  "parameters": {
                      "type": "object",
                      "properties": {
                          "url": {"type": "string", "description": "目标 URL"},
                      },
                      "required": ["url"],
                  },
              },
          }

        扩展性：
          当某个 Skill 定义了 param_schema 时使用自定义 schema，
          否则使用通用 {"type": "object"} 兜底。
        """
        tools: list[dict[str, Any]] = []
        for skill in self.get_visible_skills():
            schema = getattr(skill, "param_schema", None)
            if schema is None:
                schema = {"type": "object"}
            tools.append({
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": schema,
                },
            })
        return tools

    def get_tool_names(self) -> list[str]:
        """获取 LLM 可见的工具名称列表（轻量版，用于日志/调试）"""
        return [s.name for s in self.get_visible_skills()]

    # ---- 自动推断 ----

    @staticmethod
    def detect_required_tiers(text: str) -> list[SkillTier]:
        """
        分析文本（任务描述/计划步骤），推断需要解锁的 Tier

        静态方法，不依赖 selector 实例状态。
        由 Agent Planner 在生成步骤时调用。

        Args:
            text: 用户任务描述或计划步骤文本

        Returns:
            按优先级排序的 Tier 列表（CORE 除外，CORE 始终可用）
        """
        lower = text.lower()
        detected: set[SkillTier] = set()

        for tier, keywords in TIER_KEYWORDS.items():
            if tier == SkillTier.CORE:
                continue  # CORE 不需要检测，始终可用
            for keyword in keywords:
                if keyword.lower() in lower:
                    detected.add(tier)
                    break  # 一个 Tier 只需命中一个关键词

        # 按风险等级排序返回（先低后高）
        return sorted(detected, key=lambda t: list(SkillTier).index(t))

    @staticmethod
    def estimate_tier_description(tier: SkillTier) -> str:
        """获取 Tier 的人类可读描述"""
        return TIER_DESCRIPTIONS.get(tier, tier.value)

    # ---- Skill 文档加载 ----

    def get_skill_doc(self, name: str) -> str | None:
        """
        获取 Skill 的详细文档（skill.md）

        当 Agent 决定使用某个 Skill 时调用此方法，
        将完整的 skill.md 内容注入 LLM 上下文供其参考。

        文档按需加载（lazy loading），只在 Agent 选中 skill 后读取，
        避免一次性加载所有 skill.md 浪费 token。

        Args:
            name: Skill 名称（如 "browser_click"）

        Returns:
            skill.md 的完整文本内容，文件不存在时返回 None
        """
        skill = self.get_skill(name)
        if skill is None:
            return None
        return load_skill_doc(name)


# ---- 快捷函数 ----

def create_default_selector() -> SkillSelector:
    """创建一个使用默认配置的 SkillSelector（仅 CORE 可见）"""
    return SkillSelector()


def load_skill_doc(name: str) -> str | None:
    """
    加载指定 Skill 的 markdown 文档（skill.md）

    从 descriptions/ 目录读取 {name}.md 文件。
    与 SkillSelector.get_skill_doc() 的区别：
      此函数不做 Tier 可见性检查，直接读文件。

    Args:
        name: Skill 名称（如 "browser_goto"）

    Returns:
        skill.md 文本内容，文件不存在时返回 None
    """
    doc_path = os.path.join(_SKILL_DOC_DIR, f"{name}.md")
    if not os.path.isfile(doc_path):
        logger.warning("Skill 文档不存在: %s (%s)", name, doc_path)
        return None
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as exc:
        logger.error("读取 Skill 文档失败: %s, error=%s", name, exc)
        return None
