"""
File Skills — 文件系统操作技能

风险等级：
  L3: 文件读取/写入（需要确认）

安全策略：
  根据 ShadowOS 规范，文件路径需要检查：
  - 禁止读取敏感系统文件（/etc/passwd, /etc/shadow 等）
  - 写入操作限定在沙箱工作区内
  - 真实文件 I/O 由 Phase 5 沙箱实现
"""
import logging
from app.skills.base import BaseSkill, SkillCategory, SkillContext, SkillResult
from app.skills.enums import RiskLevel

logger = logging.getLogger(__name__)

# 禁止读取的敏感文件路径关键词
SENSITIVE_FILE_KEYWORDS = [
    "/etc/",           # 系统配置文件（含 /etc/passwd, /etc/shadow, /etc/ssh/ 等）
    "/root/",          # root 用户目录
    "/.ssh/",          # SSH 私钥目录
    "/.git/",          # Git 仓库（泄露源码）
    "/var/log/",       # 系统日志
]


def _is_sensitive_path(filepath: str) -> bool:
    """检查文件路径是否为敏感系统文件"""
    lower_path = filepath.lower().replace("\\", "/")
    for keyword in SENSITIVE_FILE_KEYWORDS:
        if keyword in lower_path:
            return True
    return False


# ====================================================================
# L3 — 文件操作
# ====================================================================


class ReadFileSkill(BaseSkill):
    """读取文件内容（文件操作，L3）"""
    name = "file_read"
    description = "读取指定路径的文件内容"
    category = SkillCategory.FILE
    risk_level = RiskLevel.L3_FILE_OP

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        filepath = params.get("path", "")
        if not filepath:
            return SkillResult.fail("缺少必要参数: path")

        # 安全检查：禁止读取敏感文件
        if _is_sensitive_path(filepath):
            logger.warning("禁止读取敏感文件: %s", filepath)
            return SkillResult.fail(f"安全策略禁止读取敏感文件: {filepath}")

        # Phase 5 接入沙箱文件系统
        return SkillResult.ok(data={"path": filepath, "size": 0, "content_preview": ""})


class WriteFileSkill(BaseSkill):
    """写入文件（文件操作，L3）"""
    name = "file_write"
    description = "将内容写入到指定文件"
    category = SkillCategory.FILE
    risk_level = RiskLevel.L3_FILE_OP

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        filepath = params.get("path", "")
        content = params.get("content", "")
        if not filepath:
            return SkillResult.fail("缺少必要参数: path")

        # 安全检查：禁止写入系统敏感路径
        if _is_sensitive_path(filepath):
            return SkillResult.fail(f"安全策略禁止写入敏感路径: {filepath}")

        return SkillResult.ok(data={
            "path": filepath,
            "content_length": len(str(content)),
            "status": "write_scheduled",
        })
