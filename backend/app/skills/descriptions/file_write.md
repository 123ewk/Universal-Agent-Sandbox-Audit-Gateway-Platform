# Skill: file_write

## Description
将内容写入到指定文件。用于保存执行结果、生成报告、下载网页内容、创建脚本等。

文件写入操作受 FILE Tier 保护和敏感路径黑名单限制。写入的目标路径必须在沙箱工作区内。

## When to use
- 保存 browser_extract_text 提取的页面内容到文件
- 将任务执行结果保存为 JSON/CSV/TXT 格式
- 创建脚本文件（Python、Shell 等）
- 生成报告或日志文件
- 下载保存页面数据

## When NOT to use
- 覆盖或修改系统文件 → 会被安全策略拦截
- 只需要读取文件 → 使用 **file_read**
- 需要执行 Shell 命令 → 先写入脚本文件，再使用 **shell_run** 执行

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| path | string | yes | — | 目标文件路径。使用绝对路径（如 `/tmp/output.txt`）或相对于沙箱工作区的路径（如 `./results/data.json`）。如果文件已存在，会被覆盖 |
| content | string | yes | — | 要写入的文件内容。可以是纯文本、JSON 字符串、代码等。使用 UTF-8 编码 |

## Returns
```json
{
  "success": true,
  "data": {
    "path": "/tmp/output.txt",
    "content_length": 2048,
    "status": "write_scheduled"
  }
}
```
- `content_length`：写入的字符数

## Risk Level
L3 — 文件操作，需要用户确认

## Human Approval
Required: false（但需要用户确认解锁 FILE Tier）

## Security Rules
**禁止写入的系统敏感路径**（严格黑名单匹配）：
| 路径关键词 | 拦截原因 | 示例 |
|-----------|---------|------|
| `/etc/` | 防止配置篡改、crontab 注入 | `/etc/cron.d/evil`, `/etc/passwd` |
| `/root/` | 防止 root 文件篡改 | `/root/.bashrc` |
| `/.ssh/` | 防止 SSH 密钥替换 | `/home/user/.ssh/authorized_keys` |
| `/.git/` | 防止 Git hook 注入 | `.git/hooks/pre-commit` |
| `/var/log/` | 防止日志伪造 | `/var/log/auth.log` |

## Examples

**保存提取的页面内容：**
```
browser_extract_text(selector="article")
→ text_length: 5432, content_preview: "..."
# 保存到文件供后续处理
file_write(path="/tmp/article.txt", content="...完整的文本内容...")
```

**保存结构化数据：**
```
file_write(
    path="/tmp/results.json",
    content='{"title": "Example", "price": 29.99, "in_stock": true}'
)
```

**创建 Python 脚本：**
```
file_write(
    path="/tmp/process.py",
    content="import json\nwith open('data.json') as f:\n    data = json.load(f)\nprint(len(data))"
)
```

## Limits
timeout: 5s
max_retry: 2

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: path` | 未提供 path | 检查调用参数 |
| `缺少必要参数: content` | 未提供 content | 如果要写入空文件，传入 `content=""` |
| `安全策略禁止写入敏感路径: ...` | 试图写入受保护的系统路径 | 路径包含敏感关键词，使用沙箱工作区路径（如 `/tmp/`） |
| `写入失败` | 磁盘空间不足、权限不足、路径不存在 | 确认目录是否存在，检查磁盘空间 |
