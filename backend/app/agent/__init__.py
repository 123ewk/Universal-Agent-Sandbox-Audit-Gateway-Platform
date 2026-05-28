"""
Agent 编排引擎 — Phase 5 核心模块

Plan-Execute-Observe-Reflect 循环 + 七层上下文管理 + pgvector 向量记忆

模块结构：
  state.py:       AgentState + StepRecord + PlanStep（Pydantic 状态模型）
  context.py:     ContextManager — 七层上下文组装
  observation.py: ObservationPipeline — DOM→摘要压缩流水线
  compression.py: ContextCompressor — Working Context 裁剪 + 增量摘要
  prompts.py:     SystemPrompt / PlanPrompt / ReflectPrompt 模板
  llm.py:         LLMClient — 多模型工厂（OpenAI/DeepSeek/Claude）
  graph.py:       AgentGraph — LangGraph 状态图
  router.py:      FastAPI REST + WebSocket 端点
"""
