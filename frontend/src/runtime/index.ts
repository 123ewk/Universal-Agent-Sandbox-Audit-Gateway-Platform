/**
 * Runtime Layer — 统一入口
 *
 * 连接 WS Client → EventBus → Reducers → Pinia Stores
 *
 * 使用方式：
 *   import { createRuntime } from '@/runtime'
 *   const runtime = createRuntime(sessionsStore, approvalsStore)
 *   runtime.connect(sessionId)
 */
export { WSClient } from './ws-client'
export { EventBus } from './event-bus'
export type { ReducerFn } from './event-bus'
export { createSessionReducer } from './reducers/session.reducer'
export { createStepReducer } from './reducers/step.reducer'
export { createApprovalReducer } from './reducers/approval.reducer'
export { createSandboxReducer } from './reducers/sandbox.reducer'
export { EventType, getEventPrefix } from './event-types'
export type {
  WSMessage,
  WSPayload,
  StepPayload,
  NavigationPayload,
  ScreenshotPayload,
  RiskPayload,
  ApprovalPayload,
  PageInfoPayload,
  CostPayload,
  PlanStep,
} from './event-types'
