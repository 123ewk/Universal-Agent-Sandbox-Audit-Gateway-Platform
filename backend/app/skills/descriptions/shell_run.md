# Skill: shell_run

## Description
在沙箱环境中执行 Shell 命令。用于安装依赖、运行脚本、文件操作、系统管理等。

## Capability
- 执行 Shell 命令并获取 stdout/stderr 输出
- 运行 Python/Node/Shell 脚本
- 安装 npm/pip 包
- 使用 git 进行版本控制操作
- 文件系统操作（ls, cp, mv, grep 等）
- 编译和运行代码

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| command | string | yes | 要执行的 Shell 命令。可以是单行命令或多行脚本 |

## Returns
```json
{
  "success": true,
  "data": {
    "command": "ls -la",
    "stdout": "total 24\ndrwxr-xr-x ...",
    "stderr": "",
    "exit_code": 0
  }
}
```

## Risk Level
L4 — Shell 执行，需要人工审批

## Human Approval
Required: true — 每次执行都需要人工审批

## Security Rules
**禁止执行的命令**（黑名单拦截）：
| 类型 | 示例 |
|------|------|
| 数据破坏 | `rm -rf /`, `dd if=`, `mkfs`, `format` |
| 权限滥用 | `chmod 777`, `chown`, `passwd` |
| Fork 炸弹 | `:(){ \|:& };:`, `fork bomb` |
| 网络攻击 | `nmap`, `hydra`, `sqlmap` |
| 数据外泄 | `wget`, `curl`, `nc`（外连工具） |

## Limits
timeout: 60s
max_retry: 1（失败不自动重试，需人工确认后重新发起）

## Errors
- `缺少必要参数: command` — 未提供 command
- `命令包含禁止关键词: ...` — 触发黑名单拦截
- `命令执行超时` — 超过 60s 限制
- `退出码非零` — 命令执行失败，查看 stderr 获取详细信息

## Best Practices
- 优先使用 Python 内置操作（读取文件、处理数据）而非 shell 命令
- 安装包时指定版本号以避免意外升级
- 长任务注意 TIMEOUT 限制，拆分为多个短命令
