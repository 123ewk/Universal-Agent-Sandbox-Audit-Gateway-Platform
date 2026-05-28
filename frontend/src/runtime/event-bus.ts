/**
 * EventBus — 事件路由中枢
 *
 * 职责：
 *   1. 接收 WSClient 的 WSMessage
 *   2. 按 seq 去重（seq ≤ lastProcessed → skip）
 *   3. 按 event 前缀路由到对应 Reducer
 *   4. Reducer 处理后写入 Pinia Store
 *
 * 设计动机（用户方案）：
 *   避免巨型 onEvent if-else，通过 EventBus → Reducers 分拆事件处理。
 *   每个 Reducer 是纯状态转换函数，可独立测试。
 *
 * 使用方式：
 *   const bus = new EventBus()
 *   bus.registerReducer('agent', agentReducer)
 *   wsClient.onMessage((msg) => bus.dispatch(msg))
 */
import type { WSMessage } from './event-types'
import { getEventPrefix } from './event-types'

export type ReducerFn = (msg: WSMessage) => void

export class EventBus {
  private reducers = new Map<string, ReducerFn>()
  /** 每个 session 已处理的最后 seq，用于去重 */
  private lastSeq = new Map<number, number>()

  // ================================================================
  // Public API
  // ================================================================

  /** 注册一个 Reducer 处理某个前缀的事件 */
  registerReducer(prefix: string, reducer: ReducerFn): void {
    this.reducers.set(prefix, reducer)
  }

  /** 接收 WSMessage 并路由处理 */
  dispatch(msg: WSMessage): void {
    // seq 去重
    const sessionId = msg.session_id
    const seq = msg.seq ?? 0
    if (seq > 0) {
      const last = this.lastSeq.get(sessionId) ?? 0
      if (seq <= last) return
      this.lastSeq.set(sessionId, seq)
    }

    // 按前缀路由
    const prefix = getEventPrefix(msg.event)
    const reducer = this.reducers.get(prefix)
    if (reducer) {
      reducer(msg)
    }
  }

  /** 清除 session 的 seq 记录（session 结束或切换时调用） */
  resetSeq(sessionId: number): void {
    this.lastSeq.delete(sessionId)
  }

  /** 清除所有 reducer 和状态 */
  destroy(): void {
    this.reducers.clear()
    this.lastSeq.clear()
  }
}
