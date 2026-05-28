# Skill: browser_type

## Description
在指定的输入框中输入文本内容。模拟用户键盘输入，支持任意文本内容。

通常用于表单填写——在 browser_click 点击输入框使其获得焦点后，调用 browser_type 输入文本。输入完成后可能需要 browser_click 点击提交按钮。

## When to use
- 在搜索框中输入关键词
- 在表单字段中输入数据（用户名、邮箱、地址、备注等）
- 在文本区域中输入多行内容
- 清空输入框内容（传入空字符串）

## When NOT to use
- 点击按钮或链接 → 使用 **browser_click**
- 从页面提取文本 → 使用 **browser_extract_text**
- 需要输入密码 → 可以使用，但注意审计日志会记录输入内容（密码建议单独处理）

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| selector | string | yes | — | CSS 选择器，定位目标输入框。示例：`"#search-input"`、`"input[name='username']"`、`"textarea"`、`"form#login input[type='text']"` |
| text | string | yes | — | 要输入的文本内容。支持任意字符串，包括中文、特殊字符、多行文本。传入空字符串 `""` 可以清空输入框 |

## Returns
```json
{
  "success": true,
  "data": {
    "selector": "#search-input",
    "text_length": 11
  }
}
```

## Risk Level
L2 — 交互操作，需要审计记录

## Human Approval
Required: false

## Security Rules
- 输入的文本内容记录到审计日志，用于操作追溯
- 密码输入会记录到审计日志（标记为敏感字段），前端展示时脱敏

## Typical Task Flow

**搜索操作：**
```
Step 1: browser_goto(url="https://www.google.com")
Step 2: browser_type(selector="input[name='q']", text="Python async programming")
Step 3: browser_click(selector="input[type='submit']")
Step 4: browser_extract_text(selector="#search")
```

**表单填写：**
```
Step 1: browser_type(selector="#username", text="myuser")
Step 2: browser_type(selector="#email", text="user@example.com")
Step 3: browser_type(selector="#comment", text="这是一条备注信息\n第二行内容")
Step 4: browser_click(selector="#submit-btn")
```

## Limits
timeout: 10s
max_retry: 3

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `缺少必要参数: selector` | 未提供 selector | 检查调用参数 |
| `缺少必要参数: text` | 未提供 text 内容 | 如果要清空输入框，传入 `text=""` |
| `元素未找到: ...` | 选择器未匹配到输入框 | 先使用 browser_extract_text 或 screenshot 查看页面输入框的实际选择器 |
| `元素不可输入: ...` | 元素存在但非输入框（如 div、p 等） | 检查 selector 是否指向了正确的 input/textarea 元素 |
