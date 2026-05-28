# Skill: shell_run

## Description
在沙箱环境中执行 Shell 命令（bash/cmd）。用于安装依赖、运行脚本、文件操作、版本控制、编译代码等。

这是 Agent 最高权限的 Skill——可以执行任意命令。每次执行都需要通过 AuditGateway 的 L4 审批流程。命令执行在隔离的沙箱环境中进行，不直接影响宿主机。

## When to use
- 安装 Python/npm/apt 依赖包
- 运行已写入的脚本文件（Python、Node.js、Shell）
- 使用 git 进行版本控制操作（clone、commit、push 等）
- 文件系统操作（cp、mv、grep、find、ls、cat 等）
- 编译和运行源代码
- 数据处理和转换（使用 jq、sed、awk 等）
- 创建目录结构（mkdir -p）

## When NOT to use
- 读取文件内容 → 使用 **file_read**（更安全、有内容预览）
- 写入文件 → 使用 **file_write**（结构化内容写入）
- 执行危险命令 → 会被黑名单拦截且触发风险警报
- 安装未经验证的软件包 → 需要人工审批时仔细评估

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| command | string | yes | — | 要执行的 Shell 命令。可以是单行命令（`ls -la`），也可以是多行脚本。在沙箱环境中以默认 shell 执行 |

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
- `stdout`：标准输出内容
- `stderr`：标准错误输出内容
- `exit_code`：退出码（0 表示成功，非 0 表示失败）

## Risk Level
L4 — Shell 执行，需要人工审批

## Human Approval
Required: true — **每次执行都需要人工审批**。审批流程：
1. Agent 调用 shell_run → 命令被 AuditGateway 拦截
2. 创建 ApprovalRecord（包含完整命令和安全评估）
3. 前端弹窗显示审批请求（显示命令、风险等级、评估理由）
4. 人类审批：允许执行 / 拒绝执行 / 超时自动拒绝（默认 300s 超时）
5. 审批通过后，`execute_approved()` 重新执行命令

## Security Rules

**黑名单命令拦截**（匹配到即拦截，不可绕过）：
| 类型 | 拦截关键词 | 风险 |
|------|-----------|------|
| 数据破坏 | `rm -rf /`, `rm -rf /*`, `dd if=`, `mkfs`, `format` | 不可逆数据删除 |
| 权限滥用 | `chmod 777`, `chown`, `passwd` | 权限提升/篡改 |
| Fork 炸弹 | `:({`, `fork bomb`, `fork()` | 系统资源耗尽 |
| 端口扫描 | `nmap`, `masscan` | 网络攻击工具 |
| 密码破解 | `hydra`, `john`, `hashcat` | 密码攻击 |
| SQL 注入 | `sqlmap` | Web 攻击工具 |
| 数据外泄 | `curl`, `wget`, `nc` | 向外传输数据 |

> **注意**：普通 `rm` 和 `curl` 是安全的——`rm -rf /` 匹配的是 `rm -rf /` 带根目录路径，`curl` 匹配 `curl `（带空格），`curl` 命令本身仍可使用 `curl --help` 等非外连用法。具体拦截逻辑由后端黑名单匹配算法决定。

## Limits
timeout: 60s（长时间运行的任务如编译、大数据处理可能超时）
max_retry: 1（失败不自动重试，需人工确认后重新发起）

## Examples

**查看工作目录内容：**
```
shell_run(command="ls -la")
```

**安装依赖并运行脚本：**
```
# 先写入脚本
file_write(path="/tmp/analyze.py", content="...")
# 再安装依赖
shell_run(command="pip install requests pandas")
# 执行脚本
shell_run(command="python /tmp/analyze.py")
```

**Git 操作：**
```
shell_run(command="git clone https://github.com/example/repo.git /tmp/repo")
shell_run(command="cd /tmp/repo && git log --oneline -5")
```

**数据搜索：**
```
shell_run(command="grep -r 'TODO' /tmp/project/ --include='*.py'")
```

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: command` | 未提供 command | 检查调用参数 |
| `命令包含禁止关键词: ...` | 触发了黑名单拦截 | 命令包含危险关键词，检查是否有安全的替代方式。例如 `wget` 下载可以用 `file_write` 替代 |
| `命令执行超时` | 超过 60s 限制 | 拆分长任务为多个短命令，或优化命令执行效率 |
| `退出码非零` | 命令执行失败 | 查看 `stderr` 获取详细错误信息 |
| `审批已拒绝` | 人工审批拒绝了执行 | 任务需要重新规划 |
| `审批超时` | 300s 内未获得人工审批 | 任务被自动拒绝，需要重新发起 |

## Best Practices
- **优先使用 Python 内置操作**而非 shell 命令：文件处理用 Python 的 `open()`，数据转换用 Python 的 `json`/`csv` 模块
- **安装包时指定版本号**：`pip install requests==2.31.0` 而非 `pip install requests`，避免意外升级
- **长任务拆分为短命令**：单个命令不要超过 60s，分步执行
- **检查退出码**：命令失败时查看 `stderr` 而非 `stdout` 获取错误信息
- **脚本优先**：多步骤操作先写入脚本文件，再执行脚本，方便调试和审批
