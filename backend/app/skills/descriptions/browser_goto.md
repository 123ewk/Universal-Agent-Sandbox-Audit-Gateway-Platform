# Skill: browser_goto

## Description
导航到指定的 URL 地址。打开新页面或跳转到已有页面的新链接。

## Capability
- 打开 URL 进行页面导航
- 支持 HTTP/HTTPS 协议
- 导航完成后等待页面加载

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| url | string | yes | 目标 URL，必须以 http:// 或 https:// 开头 |

## Returns
```json
{
  "success": true,
  "data": {
    "url": "https://example.com",
    "status": "navigation_scheduled"
  }
}
```

## Risk Level
L1 — 只读操作，无副作用

## Human Approval
Required: false

## Security Rules
- 禁止 `file://` 协议（安全策略拦截本地文件访问）
- 禁止 `chrome://` `about://` 等浏览器内部协议
- 银行/支付类域名触发风险加分，高风险仍需通过 AuditGateway 审批

## Limits
timeout: 30s
max_retry: 2

## Errors
- `缺少必要参数: url` — 未提供 url 参数
- `安全策略禁止访问该 URL` — URL 被风险策略拦截
