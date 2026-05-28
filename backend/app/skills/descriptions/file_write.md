# Skill: file_write

## Description
将内容写入到指定文件。用于保存执行结果、生成报告、下载数据等。

## Capability
- 创建新文件并写入内容
- 覆盖已有文件
- 写入文本内容（UTF-8 编码）

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| path | string | yes | 目标文件路径。使用绝对路径或沙箱工作区路径 |
| content | string | yes | 要写入的文件内容 |

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

## Risk Level
L3 — 文件操作，需要用户确认

## Human Approval
Required: false（但需要用户确认解锁 FILE Tier）

## Security Rules
- **禁止写入系统敏感路径**：
  - `/etc/` — 系统配置目录（防止 crontab 注入等攻击）
  - `/root/` — root 用户目录
  - `/.ssh/` — SSH 密钥覆盖
  - `/.git/` — Git hook 注入
  - `/var/log/` — 日志伪造
- 写入路径限定在沙箱工作区内（由 Phase 5 沙箱实现）

## Limits
timeout: 5s
max_retry: 2

## Errors
- `缺少必要参数: path` — 未提供 path
- `安全策略禁止写入敏感路径: ...` — 试图写入受保护的系统路径
- `写入失败: ...` — 磁盘空间不足、权限不足等
