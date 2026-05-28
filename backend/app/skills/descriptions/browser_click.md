# Skill: browser_click

## Description
点击页面上的元素。通过 CSS 选择器定位要点击的目标——可以是按钮、链接、输入框、下拉菜单、复选框等任何可交互元素。

点击是最常用的交互操作。通常在 goto（导航到页面）和 extract_text（理解页面内容）之后调用。点击后页面可能会变化（跳转、弹窗、展开菜单等），建议点击后紧跟 screenshot 或 extract_text 确认结果。

## When to use
- 点击按钮（提交、搜索、确认、取消、删除等）
- 点击链接跳转到新页面或锚点
- 点击输入框使其获得焦点（通常先 click 再 type）
- 点击下拉菜单项、选项卡、复选框、单选按钮
- 点击弹窗/模态框的关闭按钮
- 点击分页链接翻页

## When NOT to use
- 只需要输入文本，输入框已获得焦点 → 直接使用 **browser_type**
- 需要导航到新的 URL → 使用 **browser_goto**（更直接、更可靠）
- 需要从页面提取文本信息 → 使用 **browser_extract_text**

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| selector | string | yes | — | CSS 选择器，定位要点击的元素。示例：`"#submit-btn"`、`".nav-link"`、`"button[type='submit']"`、`"a[href='/login']"`、`"div.modal > button.close"` |

选择器策略（按优先级排序）：
1. **ID 选择器**：`"#submit-btn"` — 最精确，优先使用
2. **属性选择器**：`"button[type='submit']"` — 适合没有 ID 的元素
3. **类选择器**：`".btn-primary"` — 适合有明确 class 的元素
4. **层级选择器**：`"form#login > div > button"` — 适合需要通过上下文定位的元素

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
- **禁止点击支付/金融交易的确认按钮**（如 "确认支付"、"立即付款"）：如果页面包含金额输入或支付确认按钮，RiskEngine 会触发风险加分
- **高危页面加分**：银行、支付类域名下所有点击操作均会触发风险加分
- 所有点击操作记录到审计日志：包括 selector、页面 URL、时间戳

## Examples

**点击按钮提交表单：**
```
browser_click(selector="#submit-btn")
```

**点击链接：**
```
browser_click(selector="a[href='/product/123']")
```

**先提取文本了解页面，再点击目标链接：**
```
browser_extract_text(selector=".search-results")
# 从文本中找到目标链接后
browser_click(selector="a.product-title")
```

**点击翻页：**
```
browser_click(selector="a.next-page")
# 等待新内容加载
browser_extract_text(selector=".results")
```

## Limits
timeout: 10s（等待元素出现在 DOM 中 + 点击完成）
max_retry: 3（元素可能被暂时遮挡或页面未完全加载）

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: selector` | 未提供 selector | 检查调用参数 |
| `元素未找到: ...` | CSS 选择器未匹配到任何元素 | 使用 browser_extract_text 查看页面实际 DOM 结构，调整选择器 |
| `元素不可点击: ...` | 元素存在但不可交互（被遮挡、禁用、不可见） | 元素可能在弹窗后面或未加载完成，尝试滚动或等待后重试 |
| `点击超时` | 页面在点击后没有响应 | 页面可能已跳转或卡死，尝试刷新 |
