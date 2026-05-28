/**
 * Session Reducer — 处理 agent.* 生命周期事件
 *
 * 监听：agent.started / agent.planning / agent.plan.completed
 *       agent.completed / agent.failed / agent.cancelled
 * 写入：sessions store（SessionState 的生命周期）
 */
import type { WSMessage, PlanStep, CostPayload } from '../event-types'

export interface SessionStoreLike {
  setSessionStatus(sessionId: number, status: string): void
  setPlanSteps(sessionId: number, steps: PlanStep[]): void
  setSessionResult(sessionId: number, status: string, cost: CostPayload, error: string | null): void
}

export function createSessionReducer(store: SessionStoreLike) {
  return (msg: WSMessage): void => {
    const { event, session_id: sessionId, payload } = msg

    switch (event) {
      case 'agent.started':
        store.setSessionStatus(sessionId, 'running')
        break

      case 'agent.planning':
        store.setSessionStatus(sessionId, 'planning')
        break

      case 'agent.plan.completed':
        if (payload.steps && Array.isArray(payload.steps)) {
          store.setPlanSteps(sessionId, payload.steps as unknown as PlanStep[])
        }
        break

      case 'agent.completed':
        store.setSessionResult(sessionId, 'completed', payload as unknown as CostPayload, null)
        break

      case 'agent.failed':
        store.setSessionResult(
          sessionId,
          'failed',
          payload as unknown as CostPayload,
          (payload.error as string) ?? 'Unknown error',
        )
        break

      case 'agent.cancelled':
        store.setSessionResult(sessionId, 'cancelled', payload as unknown as CostPayload, null)
        break
    }
  }
}
