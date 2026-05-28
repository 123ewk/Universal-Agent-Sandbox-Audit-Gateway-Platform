/**
 * Sessions Store — 多 Session 状态管理
 *
 * 设计要点（用户方案）：
 *   - Map<sessionId, SessionState> 而非单个 currentSession
 *   - 支持多 Session 同时监控、Resume、Replay
 *   - 每个 session 维护独立的 lastSeq 用于去重
 *   - 截图路径分离：WS 只传路径，HTTP 单独加载图片
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  PlanStep,
  StepPayload,
  ScreenshotPayload,
  PageInfoPayload,
  CostPayload,
} from '@/runtime/event-types'

// ====================================================================
// StepRecord — 单个执行步骤
// ====================================================================

export interface StepRecord {
  step_number: number
  skill_name: string
  description: string
  status: 'pending' | 'running' | 'success' | 'failed'
  execution_time_ms: number
  error: string | null
  started_at: string | null
  finished_at: string | null
}

// ====================================================================
// SessionState — 单个 Session 的完整状态
// ====================================================================

export interface SessionState {
  sessionId: number
  taskDescription: string
  status: string // 'pending' | 'planning' | 'running' | 'completed' | 'failed' | 'cancelled'
  planSteps: PlanStep[]
  executionHistory: StepRecord[]
  currentStepIndex: number
  totalStepsExecuted: number
  progressPct: number
  llmCost: string
  tokensUsed: number
  errorMessage: string | null
  lastSeq: number
  screenshots: ScreenshotPayload[]
  currentUrl: string
  pageTitle: string
  createdAt: string | null
}

function createEmptyState(sessionId: number): SessionState {
  return {
    sessionId,
    taskDescription: '',
    status: 'pending',
    planSteps: [],
    executionHistory: [],
    currentStepIndex: 0,
    totalStepsExecuted: 0,
    progressPct: 0,
    llmCost: '0',
    tokensUsed: 0,
    errorMessage: null,
    lastSeq: 0,
    screenshots: [],
    currentUrl: '',
    pageTitle: '',
    createdAt: null,
  }
}

// ====================================================================
// Store
// ====================================================================

export const useSessionsStore = defineStore('sessions', () => {
  const sessions = ref<Map<number, SessionState>>(new Map())
  const activeSessionId = ref<number | null>(null)

  // ================================================================
  // Getters
  // ================================================================

  /** 当前活跃的 Session */
  const activeSession = computed<SessionState | null>(() => {
    if (activeSessionId.value === null) return null
    return sessions.value.get(activeSessionId.value) ?? null
  })

  /** 当前步骤的计划信息 */
  const currentPlanStep = computed<PlanStep | null>(() => {
    const s = activeSession.value
    if (!s || s.currentStepIndex >= s.planSteps.length) return null
    return s.planSteps[s.currentStepIndex] ?? null
  })

  /** 最近 N 条执行记录（用于 Timeline 渲染） */
  const recentSteps = computed<StepRecord[]>(() => {
    const s = activeSession.value
    if (!s) return []
    return s.executionHistory.slice(-20)
  })

  /** 所有 Session ID 列表 */
  const sessionIds = computed<number[]>(() => {
    return Array.from(sessions.value.keys())
  })

  // ================================================================
  // Session lifecycle
  // ================================================================

  function initSession(sessionId: number, taskDescription?: string): void {
    const existing = sessions.value.get(sessionId)
    if (existing) {
      activeSessionId.value = sessionId
      return
    }
    const state = createEmptyState(sessionId)
    if (taskDescription) state.taskDescription = taskDescription
    state.status = 'running'
    state.createdAt = new Date().toISOString()
    sessions.value.set(sessionId, state)
    activeSessionId.value = sessionId
  }

  function setActiveSession(sessionId: number): void {
    if (sessions.value.has(sessionId)) {
      activeSessionId.value = sessionId
    }
  }

  function removeSession(sessionId: number): void {
    sessions.value.delete(sessionId)
    if (activeSessionId.value === sessionId) {
      const remaining = Array.from(sessions.value.keys())
      activeSessionId.value = remaining.length > 0 ? remaining[0] : null
    }
  }

  // ================================================================
  // Status updates (session.reducer)
  // ================================================================

  function setSessionStatus(sessionId: number, status: string): void {
    const s = sessions.value.get(sessionId)
    if (s) s.status = status
  }

  function setPlanSteps(sessionId: number, steps: PlanStep[]): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.planSteps = steps
    // 预填充 executionHistory 占位
    s.executionHistory = steps.map((st) => ({
      step_number: st.step_number,
      skill_name: st.skill_name,
      description: st.description,
      status: 'pending' as const,
      execution_time_ms: 0,
      error: null,
      started_at: null,
      finished_at: null,
    }))
  }

  function setSessionResult(
    sessionId: number,
    status: string,
    cost: CostPayload,
    error: string | null,
  ): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.status = status
    s.llmCost = cost.llm_cost ?? '0'
    s.tokensUsed = cost.tokens_used ?? 0
    s.progressPct = 100
    s.errorMessage = error
  }

  // ================================================================
  // Step updates (step.reducer)
  // ================================================================

  function setCurrentStep(sessionId: number, stepNumber: number): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.currentStepIndex = stepNumber
    if (s.planSteps.length > 0) {
      s.progressPct = Math.round((stepNumber / s.planSteps.length) * 100)
    }
    // 标记当前步骤为 running
    const step = s.executionHistory.find((r) => r.step_number === stepNumber)
    if (step) {
      step.status = 'running'
      step.started_at = new Date().toISOString()
    }
  }

  function updateStepResult(sessionId: number, payload: StepPayload): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    const step = s.executionHistory.find(
      (r) => r.step_number === payload.step_number,
    )
    if (!step) return
    step.status = payload.success ? 'success' : 'failed'
    step.execution_time_ms = payload.execution_time_ms
    step.error = payload.error ?? null
    step.finished_at = new Date().toISOString()
  }

  function incrementTotalSteps(sessionId: number): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.totalStepsExecuted++
  }

  // ================================================================
  // Sandbox updates (sandbox.reducer)
  // ================================================================

  function setCurrentUrl(sessionId: number, url: string, title: string): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.currentUrl = url
    s.pageTitle = title
  }

  function addScreenshot(sessionId: number, payload: ScreenshotPayload): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    s.screenshots.push(payload)
  }

  function setPageInfo(sessionId: number, payload: PageInfoPayload): void {
    const s = sessions.value.get(sessionId)
    if (!s) return
    if (payload.url) s.currentUrl = payload.url
    if (payload.title) s.pageTitle = payload.title
  }

  // ================================================================
  // Seq tracking
  // ================================================================

  function updateLastSeq(sessionId: number, seq: number): void {
    const s = sessions.value.get(sessionId)
    if (s && seq > s.lastSeq) s.lastSeq = seq
  }

  return {
    // state
    sessions,
    activeSessionId,
    // getters
    activeSession,
    currentPlanStep,
    recentSteps,
    sessionIds,
    // actions
    initSession,
    setActiveSession,
    removeSession,
    setSessionStatus,
    setPlanSteps,
    setSessionResult,
    setCurrentStep,
    updateStepResult,
    incrementTotalSteps,
    setCurrentUrl,
    addScreenshot,
    setPageInfo,
    updateLastSeq,
  }
})
