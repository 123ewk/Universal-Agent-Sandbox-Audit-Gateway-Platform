<script setup lang="ts">
/**
 * ApprovalDialog — 审批弹窗
 *
 * 当 Agent 执行高危操作触发审批时，弹窗显示风险信息，
 * 用户可批准或拒绝。审批结果通过 REST API 发送。
 *
 * 自动弹出：监听 approvalsStore.hasPending
 * 支持键盘：Enter = 批准，Escape = 拒绝
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useApprovalsStore } from '@/stores/approvals'

const approvalsStore = useApprovalsStore()

const visible = ref(false)
const denyReason = ref('')
const processing = ref(false)
const error = ref<string | null>(null)

// ================================================================
// Computed
// ================================================================

const pendingList = computed(() => approvalsStore.pending)
const current = computed(() => pendingList.value[0] ?? null)

const riskLabel = computed(() => {
  const score = current.value?.riskScore ?? 0
  if (score >= 80) return 'danger'
  if (score >= 50) return 'warning'
  return 'info'
})

const riskClass = computed(() => `ar-${riskLabel.value}`)

// ================================================================
// Watch: auto-show when pending appears
// ================================================================

watch(
  () => pendingList.value.length,
  (len) => {
    if (len > 0 && !visible.value) {
      visible.value = true
      denyReason.value = ''
      error.value = null
    }
  },
)

// ================================================================
// Keyboard shortcuts
// ================================================================

function onKeydown(e: KeyboardEvent): void {
  if (!visible.value || processing.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    // Don't close — user must explicitly deny or approve
  }
  if (e.key === 'Enter' && e.ctrlKey) {
    e.preventDefault()
    handleApprove()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// ================================================================
// Actions
// ================================================================

async function handleApprove(): Promise<void> {
  if (!current.value) return
  processing.value = true
  error.value = null
  try {
    await approvalsStore.approve(current.value.approvalId)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '批准失败'
    processing.value = false
    return
  }
  processing.value = false
  if (pendingList.value.length === 0) {
    visible.value = false
  }
}

async function handleDeny(): Promise<void> {
  if (!current.value) return
  processing.value = true
  error.value = null
  try {
    await approvalsStore.deny(current.value.approvalId, denyReason.value)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '拒绝失败'
    processing.value = false
    return
  }
  denyReason.value = ''
  processing.value = false
  if (pendingList.value.length === 0) {
    visible.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible && current" class="approval-overlay" @click.self="() => {}">
      <div class="approval-dialog card">
        <!-- Header -->
        <div class="ad-header">
          <div class="ad-icon">&#x26A0;</div>
          <div>
            <h3>操作需要审批</h3>
            <p class="ad-session">Session #{{ current.sessionId }}</p>
          </div>
        </div>

        <!-- Risk Info -->
        <div class="ad-risk" :class="riskClass">
          <div class="ad-risk-score">{{ current.riskScore }}</div>
          <div class="ad-risk-label">风险评分</div>
        </div>

        <!-- Details -->
        <dl class="ad-details">
          <dt>Skill</dt>
          <dd><code>{{ current.skillName }}</code></dd>

          <dt>Step</dt>
          <dd>{{ current.stepNumber }}</dd>

          <dt>风险原因</dt>
          <dd>
            <ul v-if="current.riskReasons.length > 0">
              <li v-for="r in current.riskReasons" :key="r">{{ r }}</li>
            </ul>
            <span v-else class="ad-no-reason">未提供</span>
          </dd>
        </dl>

        <!-- Deny Reason -->
        <div class="ad-deny-reason">
          <label for="deny-reason">拒绝原因（可选）</label>
          <input
            id="deny-reason"
            v-model="denyReason"
            class="input"
            type="text"
            placeholder="输入拒绝原因..."
            :disabled="processing"
          />
        </div>

        <!-- Pending Count -->
        <div v-if="pendingList.length > 1" class="ad-queue">
          队列中还有 {{ pendingList.length - 1 }} 个待审批请求
        </div>

        <!-- Error -->
        <p v-if="error" class="ad-error">{{ error }}</p>

        <!-- Actions -->
        <div class="ad-actions">
          <button
            class="btn btn-danger"
            :disabled="processing"
            @click="handleDeny"
          >
            拒绝
          </button>
          <button
            class="btn btn-primary"
            :disabled="processing"
            @click="handleApprove"
          >
            批准 (Ctrl+Enter)
          </button>
        </div>

        <!-- Timeout hint -->
        <p class="ad-timeout-hint">
          如不处理，将在 5 分钟后自动拒绝
        </p>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.approval-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, .6);
  backdrop-filter: blur(2px);
}

.approval-dialog {
  width: 440px;
  max-width: 90vw;
  max-height: 90vh;
  overflow-y: auto;
}

/* Header */

.ad-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.ad-icon {
  font-size: 28px;
  color: var(--warning);
}

.ad-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.ad-session {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

/* Risk Score */

.ad-risk {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: var(--radius);
  margin-bottom: 16px;
}

.ad-risk.ar-danger {
  background: rgba(248, 81, 73, .1);
  border: 1px solid rgba(248, 81, 73, .3);
}

.ad-risk.ar-warning {
  background: rgba(210, 153, 34, .1);
  border: 1px solid rgba(210, 153, 34, .3);
}

.ad-risk.ar-info {
  background: rgba(88, 166, 255, .1);
  border: 1px solid rgba(88, 166, 255, .3);
}

.ad-risk-score {
  font-size: 36px;
  font-weight: 700;
  font-family: var(--font-mono);
  line-height: 1;
}

.ar-danger .ad-risk-score { color: var(--danger); }
.ar-warning .ad-risk-score { color: var(--warning); }
.ar-info .ad-risk-score { color: var(--accent); }

.ad-risk-label {
  font-size: 12px;
  color: var(--text-secondary);
}

/* Details */

.ad-details {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 8px 12px;
  margin-bottom: 16px;
}

.ad-details dt {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-align: right;
}

.ad-details dd {
  font-size: 13px;
  color: var(--text-primary);
}

.ad-details code {
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 1px 6px;
  background: var(--bg-primary);
  border-radius: 3px;
  color: var(--accent);
}

.ad-details ul {
  list-style: disc;
  padding-left: 16px;
}

.ad-details li {
  font-size: 12px;
  color: var(--warning);
  line-height: 1.6;
}

.ad-no-reason {
  color: var(--text-muted);
  font-style: italic;
}

/* Deny Reason */

.ad-deny-reason {
  margin-bottom: 12px;
}

.ad-deny-reason label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

/* Queue */

.ad-queue {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  text-align: center;
}

/* Error */

.ad-error {
  font-size: 12px;
  color: var(--danger);
  margin-bottom: 8px;
}

/* Actions */

.ad-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
}

.ad-timeout-hint {
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
}
</style>
