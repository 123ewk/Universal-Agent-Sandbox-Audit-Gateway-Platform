/**
 * API Client — axios 实例 + REST 端点
 *
 * 统一封装，与后端 APIResponse[T] 结构对应：
 *   { code: "SUCCESS", message: "...", data: T }
 */
import axios from 'axios'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

// ====================================================================
// Types
// ====================================================================

export interface APIResponse<T> {
  code: string
  message: string
  data: T
}

export interface TaskCreateRequest {
  task_description: string
  max_steps?: number
}

export interface TaskCreateResult {
  task_id: number
  session_id: number
  status: string
  message: string
}

export interface TaskStatus {
  task_id: number
  session_id: number
  task_description: string
  status: string
  progress_pct: number
  current_step: number
  total_steps: number
  total_steps_executed: number
  llm_cost: string
  error_message: string | null
  created_at: string | null
  updated_at: string | null
}

export interface PendingApproval {
  id: number
  session_id: number
  skill_name: string
  step_number: number
  risk_score: number
  risk_reasons: string[]
  status: string
  created_at: string
  expires_at: string | null
}

// ====================================================================
// API endpoints
// ====================================================================

/** 创建并启动 Agent 任务 */
export async function createTask(req: TaskCreateRequest): Promise<TaskCreateResult> {
  const res = await client.post<APIResponse<TaskCreateResult>>('/tasks', req)
  return res.data.data
}

/** 查询任务状态 */
export async function getTaskStatus(taskId: number): Promise<TaskStatus> {
  const res = await client.get<APIResponse<TaskStatus>>(`/tasks/${taskId}`)
  return res.data.data
}

/** 列出所有任务 */
export async function listTasks(limit = 20, offset = 0): Promise<TaskStatus[]> {
  const res = await client.get<APIResponse<TaskStatus[]>>('/tasks', {
    params: { limit, offset },
  })
  return res.data.data
}

/** 获取待审批列表 */
export async function getPendingApprovals(sessionId?: number): Promise<{ count: number; items: PendingApproval[] }> {
  const res = await client.get('/approvals/pending', {
    params: sessionId ? { session_id: sessionId } : {},
  })
  return res.data
}

/** 批准审批请求 */
export async function approveRequest(approvalId: number): Promise<void> {
  await client.post(`/approvals/${approvalId}/approve`)
}

/** 拒绝审批请求 */
export async function denyRequest(approvalId: number, reason?: string): Promise<void> {
  await client.post(`/approvals/${approvalId}/deny`, { reason: reason ?? '' })
}

// ====================================================================
// Screenshot endpoints
// ====================================================================

export interface ScreenshotInfo {
  filename: string
  step_number: number
  action: string
  size_bytes: number
}

export interface ScreenshotList {
  session_id: number
  screenshots: ScreenshotInfo[]
  count: number
}

/** 获取 Session 的截图列表（截图路由在 /api/screenshots，非 /api/v1） */
export async function getScreenshotList(sessionId: number): Promise<ScreenshotList> {
  const res = await axios.get<ScreenshotList>('/api/screenshots/', {
    params: { session_id: sessionId },
  })
  return res.data
}

/** 构建截图 URL */
export function screenshotUrl(sessionId: number, filename: string): string {
  return `/api/screenshots/${filename}?session_id=${sessionId}`
}

export default client
