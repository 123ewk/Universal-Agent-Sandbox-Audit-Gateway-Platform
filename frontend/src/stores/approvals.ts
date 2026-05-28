/**
 * Approvals Store — 审批请求管理
 *
 * 管理待审批请求列表，由 approval.reducer 写入，
 * ApprovalDialog 组件消费。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ApprovalPayload } from '@/runtime/event-types'

// ====================================================================
// ApprovalItem — 单个审批请求
// ====================================================================

export interface ApprovalItem {
  approvalId: number
  sessionId: number
  skillName: string
  riskScore: number
  riskReasons: string[]
  stepNumber: number
  status: 'pending' | 'approved' | 'denied' | 'timeout'
  createdAt: string
}

// ====================================================================
// Store
// ====================================================================

export const useApprovalsStore = defineStore('approvals', () => {
  const items = ref<Map<number, ApprovalItem>>(new Map())

  // ================================================================
  // Getters
  // ================================================================

  /** 待审批列表 */
  const pending = computed<ApprovalItem[]>(() => {
    return Array.from(items.value.values()).filter((a) => a.status === 'pending')
  })

  /** 按 sessionId 过滤的待审批 */
  function pendingForSession(sessionId: number): ApprovalItem[] {
    return pending.value.filter((a) => a.sessionId === sessionId)
  }

  /** 是否有待审批 */
  const hasPending = computed<boolean>(() => pending.value.length > 0)

  // ================================================================
  // Actions (approval.reducer 调用)
  // ================================================================

  function addApproval(sessionId: number, payload: ApprovalPayload): void {
    items.value.set(payload.approval_id, {
      approvalId: payload.approval_id,
      sessionId,
      skillName: payload.skill_name,
      riskScore: payload.risk_score,
      riskReasons: payload.risk_reasons ?? [],
      stepNumber: payload.step_number,
      status: 'pending',
      createdAt: new Date().toISOString(),
    })
  }

  function removeApproval(approvalId: number, status: string): void {
    const item = items.value.get(approvalId)
    if (item) {
      item.status = status as ApprovalItem['status']
    }
    // 保留已处理的审批 30 秒后自动清理
    setTimeout(() => {
      items.value.delete(approvalId)
    }, 30000)
  }

  /** 手动批准（调用 REST API） */
  async function approve(approvalId: number): Promise<void> {
    const { default: axios } = await import('@/api/client')
    await axios.post(`/api/v1/approvals/${approvalId}/approve`)
  }

  /** 手动拒绝（调用 REST API） */
  async function deny(approvalId: number, reason?: string): Promise<void> {
    const { default: axios } = await import('@/api/client')
    await axios.post(`/api/v1/approvals/${approvalId}/deny`, { reason: reason ?? '' })
  }

  return {
    items,
    pending,
    hasPending,
    pendingForSession,
    addApproval,
    removeApproval,
    approve,
    deny,
  }
})
