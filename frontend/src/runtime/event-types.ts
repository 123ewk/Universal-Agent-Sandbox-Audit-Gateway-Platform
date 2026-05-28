/**
 * WebSocket 事件协议 — 与后端 EventType 枚举一一对应
 *
 * 命名空间分层：
 *   agent.*     — Agent 生命周期事件
 *   sandbox.*   — 沙箱页面事件
 *   audit.*     — 审计事件
 *   approval.*  — 审批事件
 *   system.*    — 系统事件（心跳、连接状态）
 *
 * 前端按前缀路由到不同 Reducer → Store → UI：
 *   agent.*    → step.reducer + session.reducer → sessions store → StepTimeline
 *   sandbox.*  → sandbox.reducer → sessions store → BrowserPanel
 *   audit.*    → (audit panel, Phase 8 P2)
 *   approval.* → approval.reducer → approvals store → ApprovalDialog
 *   system.*   → (connection status)
 */

// ====================================================================
// Event Types (matches backend EventType enum)
// ====================================================================

export const EventType = {
  // agent.*
  AGENT_STARTED: 'agent.started',
  AGENT_PLANNING: 'agent.planning',
  AGENT_PLAN_COMPLETED: 'agent.plan.completed',
  AGENT_STEP_STARTED: 'agent.step.started',
  AGENT_STEP_COMPLETED: 'agent.step.completed',
  AGENT_STEP_FAILED: 'agent.step.failed',
  AGENT_COMPLETED: 'agent.completed',
  AGENT_FAILED: 'agent.failed',
  AGENT_CANCELLED: 'agent.cancelled',

  // sandbox.*
  SANDBOX_NAVIGATION: 'sandbox.navigation',
  SANDBOX_SCREENSHOT: 'sandbox.screenshot',
  SANDBOX_ELEMENT_INTERACTION: 'sandbox.element.interaction',
  SANDBOX_PAGE_INFO: 'sandbox.page.info',

  // audit.*
  AUDIT_RISK_DETECTED: 'audit.risk.detected',
  AUDIT_ALERT: 'audit.alert',
  AUDIT_LOG_CREATED: 'audit.log.created',

  // approval.*
  APPROVAL_REQUIRED: 'approval.required',
  APPROVAL_APPROVED: 'approval.approved',
  APPROVAL_DENIED: 'approval.denied',
  APPROVAL_TIMEOUT: 'approval.timeout',

  // system.*
  SYSTEM_HEARTBEAT: 'system.heartbeat',
  SYSTEM_CONNECTED: 'system.connected',
  SYSTEM_DISCONNECTED: 'system.disconnected',
  SYSTEM_ERROR: 'system.error',
} as const

export type EventTypeValue = (typeof EventType)[keyof typeof EventType]

// ====================================================================
// Payload Types (matches backend Pydantic payload models)
// ====================================================================

export interface StepPayload {
  step_number: number
  skill_name: string
  description: string
  success: boolean
  execution_time_ms: number
  error: string | null
}

export interface NavigationPayload {
  from_url: string
  to_url: string
  title: string
  status_code: number | null
}

export interface ScreenshotPayload {
  path: string
  filename: string
  size_bytes: number
  step_number: number
}

export interface RiskPayload {
  risk_score: number
  risk_level: number
  reasons: string[]
  requires_approval: boolean
}

export interface ApprovalPayload {
  approval_id: number
  skill_name: string
  risk_score: number
  risk_reasons: string[]
  step_number: number
}

export interface PageInfoPayload {
  url: string
  title: string
  element_count: number
  screenshot_path: string | null
}

export interface CostPayload {
  llm_cost: string
  tokens_used: number
  total_steps: number
}

export interface PlanStep {
  step_number: number
  description: string
  skill_name: string
  skill_params: Record<string, unknown>
  expected_outcome: string
}

export type WSPayload = Record<string, unknown>

// ====================================================================
// WSMessage — unified message from backend
// ====================================================================

export interface WSMessage {
  event: string
  session_id: number
  timestamp: string
  seq?: number
  payload: WSPayload
}

// ====================================================================
// Event routing — which reducer handles which event prefix
// ====================================================================

export type EventHandler = (msg: WSMessage) => void

export const EVENT_PREFIX_MAP: Record<string, string> = {
  'agent.': 'agent',
  'sandbox.': 'sandbox',
  'audit.': 'audit',
  'approval.': 'approval',
  'system.': 'system',
}

export function getEventPrefix(event: string): string {
  for (const prefix of Object.keys(EVENT_PREFIX_MAP)) {
    if (event.startsWith(prefix)) return EVENT_PREFIX_MAP[prefix]
  }
  return 'unknown'
}
