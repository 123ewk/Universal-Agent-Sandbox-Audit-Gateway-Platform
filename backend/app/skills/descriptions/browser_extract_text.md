# Skill: browser_extract_text

## Description
获取当前页面或指定区域的所有可见文本内容。返回纯文本（不含 HTML 标签、样式、脚本），供 LLM 理解页面信息、数据提取、内容分析。

这是 Agent 获取页面信息的主要手段。文本提取的结果可以直接用于 LLM 决策——判断页面是否包含目标信息、是否需要进一步操作。

## When to use
- 需要理解当前页面的文本内容
- 搜索、查询结果的文本提取
- 文章、文档等文本密集型页面的内容获取
- 表单字段标签和值的提取

## When NOT to use
- 需要视觉信息（布局、颜色、图片、图标）→ 使用 **browser_screenshot**
- 已经知道页面内容，只需要点击某个元素 → 直接使用 **browser_click**
- 页面是纯图片/PDF → screenshot 更合适

## Parameters
| name | type | required | default | description |
|------|------|----------|---------|-------------|
| selector | string | no | `"body"` | CSS 选择器，限定提取区域。不传则提取 body 全部可见文本。使用 selector 缩小范围可以节省 token 消耗。示例：`"#main-content"`、`"article"`、`".search-results"` |

## Returns
```json
{
  "success": true,
  "data": {
    "selector": "#main-content",
    "text_length": 15342,
    "content_preview": "页面文本的前 200 字预览..."
  }
}
```
- `text_length`：提取的文本总字符数
- `content_preview`：前 200 字预览，用于快速判断内容是否符合预期
- 完整文本内容通过 `content_preview` + 后续 Memory Compression 模块提供

## Risk Level
L1 — 只读操作，无副作用

## Human Approval
Required: false

## Security Rules
- 提取的文本内容记录到审计日志，包含页面 URL 和提取区域选择器
- 不会触发跨域请求——提取仅限于当前页面的 DOM 内容
- 提取的文本大小受 timeout 限制，超大页面可能截断

## Usage Notes
- 提取的文本可能很大（数千字），LLM 消费前建议通过 Memory Compression 做摘要
- **尽可能使用 selector 缩小范围**：例如提取搜索结果的文本用 `".result"`，提取文章正文用 `"article"`，避免提取整个页面的导航、广告等无关内容
- extract_text 和 screenshot 互补：先 screenshot 确认页面布局，再 extract_text 获取详细信息
- 如果页面是动态加载的（无限滚动、分页加载），可能需要配合 click 或 scroll 先加载更多内容

## Examples

**提取文章正文：**
```
browser_extract_text(selector="article")
```

**提取搜索结果：**
```
browser_goto(url="https://www.google.com/search?q=python+async")
browser_extract_text(selector="#search")
```

**先截图再提取（视觉确认 + 文本获取）：**
```
browser_screenshot()
browser_extract_text(selector=".main-content")
```

## Limits
timeout: 10s
max_retry: 1（如果 selector 不匹配，降级返回 body 全部文本）

## Errors
| error | meaning | resolution |
|-------|---------|------------|
| `选择器未匹配` | CSS 选择器未匹配到任何元素 | 降级返回 body 全部文本，不会失败 |
| `提取超时` | 页面文本量过大 | 尝试使用更精确的 selector 缩小范围 |
| `页面未加载` | 浏览器没有打开任何页面 | 先调用 browser_goto 导航到目标页面 |
