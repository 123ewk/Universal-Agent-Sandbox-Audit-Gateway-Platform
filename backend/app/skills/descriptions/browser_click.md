# Skill: browser_click

## Description
点击页面上的元素。通过 CSS 选择器定位要点击的目标（按钮、链接、输入框等）。

## Capability
- 点击按钮（提交、确认、取消等）
- 点击链接进行页面跳转
- 点击输入框使其获得焦点
- 点击下拉菜单项、选项卡、复选框等交互元素

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| selector | string | yes | CSS 选择器，定位要点击的元素（如 `#submit-btn`、`.nav-link`、`button[type="submit"]`） |

## Returns
```json
{
  "success": true,
  "data": {
    "selector": "#submit-btn",
    "status": "click_scheduled"
  }
}
```

## Risk Level
L2 — 交互操作，需要审计记录

## Human Approval
Required: false

## Security Rules
- 禁止点击支付/金融交易类确认按钮（根据风险策略动态判定）
- 对高危页面（如银行网站）的点击操作会触发风险加分

## Limits
timeout: 10s
max_retry: 3

## Errors
- `缺少必要参数: selector` — 未提供 selector
- `元素未找到: ...` — 选择器未匹配到任何元素
- `元素不可点击: ...` — 元素存在但不可交互（被遮挡、禁用）
