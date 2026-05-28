# Skill: browser_goto

## Description
导航到指定的 URL 地址。打开新页面或跳转到已有页面的新链接。导航完成后自动等待页面加载。

这是 Agent 执行浏览器任务的第一步——在导航到目标页面之后，才能使用 click / type / extract_text / screenshot 等其他浏览器技能。

## When to use
- 需要打开一个新页面时
- 需要在当前标签页跳转到新 URL 时
- 任务开始时，作为第一个 Skill 调用

## When NOT to use
- 页面已经加载完成，只需要提取信息 → 使用 **browser_extract_text**
- 需要刷新当前页面 → 仍然可以使用 browser_goto，传入当前 URL
- 需要返回上一页 → 再次调用 browser_goto 传入上一页的 URL

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| url | string | yes | — | 目标 URL，必须以 `http://` 或 `https://` 开头。完整 URL 包括协议、域名、路径、查询参数。示例：`https://www.example.com/search?q=hello` |

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
- **禁止 `file://` 协议**：安全策略拦截本地文件访问（`file:///etc/passwd`）
- **禁止浏览器内部协议**：`chrome://`、`about://`、`chrome-extension://` 等浏览器内部页面
- **高危域名加分**：银行、支付类域名（如 `bank.com`, `paypal.com`）触发 RiskEngine 加分，score 可能超过 L1 范围
- **URL 黑名单**：已知的钓鱼、恶意软件站点会被直接拦截

## Examples

**打开网站：**
```
browser_goto(url="https://www.baidu.com")
```

**带搜索参数的 URL：**
```
browser_goto(url="https://www.google.com/search?q=天气+北京")
```

**分页浏览：**
```
Step 1: browser_goto(url="https://news.ycombinator.com")
Step 2: browser_extract_text(selector=".titleline")
Step 3: browser_goto(url="https://news.ycombinator.com/news?p=2")
```

## Limits
timeout: 30s（复杂页面可能需要更长时间，超时后放弃加载）
max_retry: 2（网络波动时自动重试）

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: url` | 未提供 url 参数 | 检查调用参数，确保 url 不为空 |
| `安全策略禁止访问该 URL` | URL 被风险策略拦截（file:// / chrome:// / 黑名单域名） | 使用合法的 http/https URL |
| `导航超时` | 页面 30s 内未加载完成 | 网络问题或页面过大，可以重试 |
| `DNS 解析失败` | 域名不存在 | 检查 URL 拼写是否正确 |
