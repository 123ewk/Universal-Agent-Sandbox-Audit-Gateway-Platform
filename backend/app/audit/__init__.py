"""
审计审批 — Phase 7 核心模块

Agent 可控性/可审计基础设施：行为检测、审批暂停、Human-in-the-loop

模块结构：
  policies.py:  AuditPolicy — 跨步骤行为规则（连续失败/高频重试/危险组合/Shell）
  detector.py:  BehaviorDetector — 连接 Policy 与 AgentState，WS 推送告警
  approval.py:  ApprovalManager — asyncio.Event 暂停/恢复机制
  router.py:    FastAPI 审批 REST 端点
"""
