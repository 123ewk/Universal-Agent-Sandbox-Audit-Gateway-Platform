# Skill: browser_screenshot

## Description
截取当前浏览器页面的截图。返回截图文件路径，供后续展示、存档或 LLM 视觉理解。

截图是 Agent 感知页面视觉状态的主要手段——当文本信息不足以判断页面布局、图片内容、样式变化时，使用截图获取视觉上下文。

## When to use
- 需要验证操作结果（如：点击后页面是否变化、弹窗是否出现）
- 页面包含图片、图表、表格等视觉信息
- extract_text 返回的文本不足以理解页面内容时
- 每一步执行后自动截图，用于审计和回放

## When NOT to use
- 只需要文本信息 → 使用 **browser_extract_text**（更快、更省 token）
- 需要查找特定文本 → 使用 extract_text 或搜索功能
- 连续多次截图但页面未变化 → 不需要重复截取

## Parameters
无需参数。截取当前浏览器可视区域的全屏截图。

## Returns
```json
{
  "success": true,
  "data": {
    "screenshot_path": "data/screenshots/session_1/step_3.png"
  }
}
```
`screenshot_path` 是截图文件在沙箱存储中的路径，可通过文件系统 API 或前端 WebSocket 获取。

## Risk Level
L1 — 只读操作，无副作用

## Human Approval
Required: false

## Security Rules
- 截图仅保存在沙箱隔离的存储路径中，不泄露到沙箱外部
- 只截取浏览器页面内容，不截取用户操作系统桌面或其他应用
- 截图文件默认保留 24 小时，超时自动清理

## Examples

**任务执行中截图审计：**
```
browser_goto(url="https://example.com")
browser_click(selector="#login-btn")
# 点击后截图，确认弹窗出现
browser_screenshot()
```

**数据提取前确认页面状态：**
```
browser_goto(url="https://news.ycombinator.com")
# 先截图看页面是否正常加载
browser_screenshot()
# 确认后再提取文本
browser_extract_text()
```

## Limits
timeout: 15s（页面复杂时可能需要更多时间渲染）
max_retry: 2

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `截图失败` | 浏览器页面未加载或已关闭 | 先调用 browser_goto 确保页面存在 |
| `截图超时` | 页面渲染时间过长 | 等待页面加载完成后再截图 |
