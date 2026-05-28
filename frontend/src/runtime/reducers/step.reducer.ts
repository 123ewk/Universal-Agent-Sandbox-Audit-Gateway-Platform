/**
 * Step Reducer — 处理 agent.step.* 事件
 *
 * 监听：agent.step.started / agent.step.completed / agent.step.failed
 * 写入：sessions store（executionHistory 的追加和更新）
 */
import type { WSMessage, StepPayload } from '../event-types'

export interface StepStoreLike {
  updateStepResult(sessionId: number, payload: StepPayload): void
  setCurrentStep(sessionId: number, stepNumber: number): void
  incrementTotalSteps(sessionId: number): void
}

export function createStepReducer(store: StepStoreLike) {
  /** 记录步骤开始时间，用于计算 execution_time_ms */
  const stepTimers = new Map<string, number>()

  return (msg: WSMessage): void => {
    const { event, session_id: sessionId, payload } = msg

    switch (event) {
      case 'agent.step.started': {
        const p = payload as unknown as StepPayload
        store.setCurrentStep(sessionId, p.step_number)
        // 记录开始时间
        stepTimers.set(`${sessionId}:${p.step_number}`, Date.now())
        break
      }

      case 'agent.step.completed': {
        const p = payload as unknown as StepPayload
        const key = `${sessionId}:${p.step_number}`
        const started = stepTimers.get(key)
        const elapsed = started ? Date.now() - started : p.execution_time_ms
        stepTimers.delete(key)

        store.updateStepResult(sessionId, {
          ...p,
          success: true,
          execution_time_ms: elapsed,
        })
        store.incrementTotalSteps(sessionId)
        break
      }

      case 'agent.step.failed': {
        const p = payload as unknown as StepPayload
        const key = `${sessionId}:${p.step_number}`
        const started = stepTimers.get(key)
        const elapsed = started ? Date.now() - started : p.execution_time_ms
        stepTimers.delete(key)

        store.updateStepResult(sessionId, {
          ...p,
          success: false,
          execution_time_ms: elapsed,
        })
        store.incrementTotalSteps(sessionId)
        break
      }
    }
  }
}
