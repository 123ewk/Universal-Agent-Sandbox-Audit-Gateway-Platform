"""
Shell Skills — Shell 命令执行技能

风险等级：
  L4: Shell 执行（需要人工审批）

安全策略：
  禁止执行的危险命令/关键词（黑名单匹配）：
  - 删除命令：rm -rf, dd, mkfs, format
  - 系统修改：chmod 777, chown, passwd
  - 网络攻击：nmap, hydra, sqlmap
  - Fork bomb：:(){:|:&};

  真实命令执行由 Phase 5 沙箱实现（Docker 隔离）。
"""
import logging
from app.skills.base import BaseSkill, SkillCategory, SkillContext, SkillResult
from app.skills.enums import RiskLevel, SkillTier

logger = logging.getLogger(__name__)

# 禁止执行的命令关键词（大小写不敏感匹配）
BLOCKED_COMMAND_KEYWORDS = [
    "rm -rf /", "rm -rf /*", "dd if=", "mkfs", "format",
    "chmod 777", "chown ", "passwd",
    ":(){", "fork bomb", "fork()",
    "nmap", "hydra", "sqlmap",
    "wget ", "curl ", "nc ",  # 网络外连工具可能被用于数据外泄
]


def _is_blocked_command(command: str) -> tuple[bool, str]:
    """检查命令是否被黑名单拦截"""
    lower_cmd = command.lower().strip()
    for keyword in BLOCKED_COMMAND_KEYWORDS:
        if keyword in lower_cmd:
            return True, f"命令包含禁止关键词: {keyword}"
    return False, ""


# ====================================================================
# L4 — Shell 操作
# ====================================================================


class RunCommandSkill(BaseSkill):
    """执行 Shell 命令（Shell 执行，L4，需人工审批）"""
    name = "shell_run"
    description = "在沙箱环境中执行 Shell 命令"
    category = SkillCategory.SHELL
    tier = SkillTier.SHELL
    risk_level = RiskLevel.L4_SHELL

    async def execute(self, context: SkillContext, **params) -> SkillResult:
        command = params.get("command", "")
        if not command:
            return SkillResult.fail("缺少必要参数: command")

        # 安全检查：黑名单命令拦截
        blocked, reason = _is_blocked_command(command)
        if blocked:
            logger.warning("Shell 命令被拦截: command=%s, reason=%s", command, reason)
            return SkillResult.fail(reason)

        # Phase 5 接入沙箱 Docker executor
        return SkillResult.ok(data={
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
        })
