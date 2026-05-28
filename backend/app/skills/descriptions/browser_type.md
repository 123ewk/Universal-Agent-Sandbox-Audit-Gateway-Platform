# Skill: browser_type

## Description
在指定的输入框中输入文本内容。模拟用户键盘输入，支持任意文本。

## Capability
- 在搜索框中输入关键词
- 在表单字段中输入数据（用户名、备注、地址等）
- 在文本区域输入多行内容
- 支持清空已有内容后再输入
- 支持密码输入（不记录明文到审计日志之外）

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| selector | string | yes | CSS 选择器，定位目标输入框 |
| text | string | yes | 要输入的文本内容 |

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
- 输入的文本内容记录到审计日志（不含密码敏感字段标记）

## Limits
timeout: 10s
max_retry: 3

## Errors
- `缺少必要参数: selector` — 未提供 selector
- `缺少必要参数: text` — 未提供 text 内容
- `元素未找到: ...` — 选择器未匹配到输入框
