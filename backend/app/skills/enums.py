"""
技能枚举定义（共享模块）

分离到单独文件的原因：
  RiskLevel 被 skills.base 和 engine.risk 双向引用，
  放在独立文件中避免循环依赖。
"""
import enum


class SkillCategory(str, enum.Enum):
    """技能分类"""
    BROWSER = "browser"
    FILE = "file"
    SHELL = "shell"
    API = "api"
    DATABASE = "database"
    SYSTEM = "system"


class SkillTier(str, enum.Enum):
    """
    渐进式披露层级

    决定 Skill 在 Agent 执行流程中何时对 LLM 可见。
    不是安全等级（安全等级走 RiskLevel），而是"使用场景"分组。

      CORE        — 始终可见的基础只读技能
      INTERACTION — 浏览器交互技能（执行过程中自动解锁）
      FILE        — 文件操作技能（需要确认后解锁）
      SHELL       — Shell 执行技能（需人工授权后解锁）

    披露逻辑：
      Agent 启动 → 只看到 CORE
      Agent 计划需要"点击/输入" → 自动解锁 INTERACTION
      Agent 提到"保存/写入" → 弹出确认 → 解锁 FILE
      Agent 需要执行命令 → 人工审批 → 解锁 SHELL
    """
    CORE = "core"
    INTERACTION = "interaction"
    FILE = "file"
    SHELL = "shell"


class RiskLevel(enum.IntEnum):
    """
    风险等级（L1-L5）

    对应 ShadowOS 的五级风险模型：
      L1:  只读操作，安全
      L2:  普通交互，需要审计
      L3:  文件操作，需要确认
      L4:  Shell 执行，需要人工审批
      L5:  高危破坏，需要人工审批 + 沙箱隔离

    为什么用 IntEnum？
      支持比较运算：L1 < L2 < L3 < L4 < L5
      支持存数据库：int 类型
      与 risk_score (0-100) 可互转：score = level * 20
    """
    L1_READONLY = 1
    L2_INTERACTION = 2
    L3_FILE_OP = 3
    L4_SHELL = 4
    L5_DESTRUCTIVE = 5
