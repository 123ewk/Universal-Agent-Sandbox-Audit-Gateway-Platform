# Skill: file_read

## Description
读取指定路径的文件内容。用于查看沙箱工作区内已保存的数据文件、配置文件、日志、脚本等。

文件读取操作受 FILE Tier 保护和敏感路径黑名单限制。读取的文件必须位于沙箱工作区内或临时目录中。

## When to use
- 查看已下载或保存的文件内容
- 读取任务生成的数据文件（JSON、CSV、TXT 等）
- 查看脚本源码
- 读取配置文件
- 检查任务输出结果

## When NOT to use
- 需要从浏览器页面提取文本 → 使用 **browser_extract_text**
- 需要写入或保存文件 → 使用 **file_write**
- 读取系统配置文件或敏感文件 → 会被安全策略拦截

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| path | string | yes | — | 文件路径。使用绝对路径（如 `/tmp/data.txt`）或相对于沙箱工作区的路径（如 `./output/result.json`）。支持正斜杠 `/` 和反斜杠 `\\` |

## Returns
```json
{
  "success": true,
  "data": {
    "path": "/tmp/data.txt",
    "size": 1024,
    "content_preview": "文件内容的前 200 字预览..."
  }
}
```
- `size`：文件字节数
- `content_preview`：文件内容的前 200 字符预览

## Risk Level
L3 — 文件操作，需要用户确认

## Human Approval
Required: false（但需要用户确认解锁 FILE Tier）

## Security Rules
**禁止读取的系统敏感文件**（严格黑名单匹配）：
| 路径关键词 | 拦截原因 | 示例 |
|-----------|---------|------|
| `/etc/` | 系统配置（包含密码、SSH 配置） | `/etc/passwd`, `/etc/shadow`, `/etc/ssh/sshd_config` |
| `/root/` | root 用户私密文件 | `/root/.bash_history`, `/root/.ssh/id_rsa` |
| `/.ssh/` | SSH 私钥泄露 | `~/.ssh/id_rsa`, `/home/user/.ssh/authorized_keys` |
| `/.git/` | 源码泄露 | `.git/config`, `.git/credentials` |
| `/var/log/` | 系统日志（可能包含敏感信息） | `/var/log/auth.log`, `/var/log/syslog` |

路径检查不区分大小写（Windows 兼容），反斜杠自动归一化为正斜杠。

## Examples

**读取任务输出文件：**
```
file_read(path="/tmp/output/data.json")
```

**读取脚本源码：**
```
file_read(path="/tmp/process.py")
```

**读取保存的页面内容：**
```
# 先保存页面内容
file_write(path="/tmp/page.txt", content="...")
# 后续再读取处理
file_read(path="/tmp/page.txt")
```

## Limits
timeout: 5s
max_retry: 2

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: path` | 未提供 path | 检查调用参数 |
| `安全策略禁止读取敏感文件: ...` | 试图读取被保护的系统文件 | 路径包含敏感关键词（如 `/etc/`），使用沙箱工作区路径 |
| `文件未找到: ...` | 路径不存在或不可读 | 使用绝对路径确认文件存在，检查文件名拼写 |
| `文件过大` | 超过单次读取大小限制 | 使用文件系统工具分块读取 |
