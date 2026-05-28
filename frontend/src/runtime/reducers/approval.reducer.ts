/**
 * Approval Reducer — 处理 approval.* 事件
 *
 * 监听：approval.required / approval.approved / approval.denied / approval.timeout
 * 写入：approvals store（审批请求的实时状态）
 */
import type { WSMessage, ApprovalPayload } from '../event-types'

export interface ApprovalStoreLike {
  addApproval(sessionId: number, payload: ApprovalPayload): void
  removeApproval(approvalId: number, status: string): void
}

export function createApprovalReducer(store: ApprovalStoreLike) {
  return (msg: WSMessage): void => {
    const { event, session_id: sessionId, payload } = msg

    switch (event) {
      case 'approval.required': {
        const p = payload as unknown as ApprovalPayload
        store.addApproval(sessionId, p)
        break
      }

      case 'approval.approved': {
        const p = payload as unknown as ApprovalPayload
        store.removeApproval(p.approval_id, 'approved')
        break
      }

      case 'approval.denied': {
        const p = payload as unknown as ApprovalPayload
        store.removeApproval(p.approval_id, 'denied')
        break
      }

      case 'approval.timeout': {
        const p = payload as unknown as ApprovalPayload
        store.removeApproval(p.approval_id, 'timeout')
        break
      }
    }
  }
}
