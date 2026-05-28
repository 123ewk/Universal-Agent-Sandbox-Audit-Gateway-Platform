# Skill: file_read

## Description
读取指定路径的文件内容。用于查看已保存的数据、配置、日志等文件。

## Capability
- 读取文本文件内容
- 读取项目沙箱内的工作文件
- 支持绝对路径和相对路径

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| path | string | yes | 文件路径。使用绝对路径或相对于沙箱工作区的路径 |

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

## Risk Level
L3 — 文件操作，需要用户确认

## Human Approval
Required: false（但需要用户确认解锁 FILE Tier）

## Security Rules
- **禁止读取系统敏感文件**：
  - `/etc/` — 系统配置（passwd, shadow, ssh 等）
  - `/root/` — root 用户目录
  - `/.ssh/` — SSH 私钥
  - `/.git/` — Git 仓库泄露源码
  - `/var/log/` — 系统日志

## Limits
timeout: 5s
max_retry: 2

## Errors
- `缺少必要参数: path` — 未提供 path
- `安全策略禁止读取敏感文件: ...` — 试图读取被保护的系统文件
- `文件未找到: ...` — 路径不存在
