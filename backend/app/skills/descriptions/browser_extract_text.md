# Skill: browser_extract_text

## Description
获取当前页面或指定区域的所有可见文本内容。用于 LLM 理解页面信息、数据提取、内容分析。

## Capability
- 提取当前页面 body 全部可见文本
- 支持 CSS 选择器限定提取区域（可选）
- 返回纯文本，不含 HTML 标签

## Parameters
| name | type | required | description |
|------|------|----------|-------------|
| selector | string | no | CSS 选择器，限定提取区域。不传则提取 body 全部文本 |

## Returns
```json
{
  "success": true,
  "data": {
    "selector": "body",
    "text_length": 15342,
    "content_preview": "页面文本的前 200 字预览..."
  }
}
```

## Risk Level
L1 — 只读操作，无副作用

## Human Approval
Required: false

## Usage Notes
- 提取的文本可能很大（数千字），LLM 消费前建议做摘要压缩
- 如果需要视觉信息（布局、颜色、图片），使用 browser_screenshot
- 优先使用 selector 缩小范围，节省 token

## Limits
timeout: 10s
max_retry: 1

## Errors
- 选择器不匹配时返回 body 全部文本作为兜底
