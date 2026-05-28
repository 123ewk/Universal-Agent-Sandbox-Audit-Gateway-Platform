# Skill: browser_screenshot

## Description
截取当前浏览器页面的全屏截图。用于视觉确认页面状态、验证操作结果。

## Capability
- 截取当前页面可见区域的截图
- 返回截图文件路径（供后续展示或存档）

## Parameters
无需参数。截取当前页面全屏截图。

## Returns
```json
{
  "success": true,
  "data": {
    "screenshot_path": "data/screenshots/session_1/step_3.png"
  }
}
```

## Risk Level
L1 — 只读操作，无副作用

## Human Approval
Required: false

## Security Rules
- 截图仅保存在沙箱隔离的存储路径中
- 不截取用户操作系统桌面或其他应用

## Limits
timeout: 15s
max_retry: 2

## Errors
- 一般不会失败。如截图异常，检查浏览器页面是否已加载完成
