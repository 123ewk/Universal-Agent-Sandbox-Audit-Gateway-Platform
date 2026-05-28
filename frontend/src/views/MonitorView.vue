<script setup lang="ts">
/**
 * MonitorView — Agent 实时监控面板
 *
 * 核心职责：连接 Session 的 WS 事件流 + 展示执行进度
 * 子组件：StepTimeline + BrowserPanel + ApprovalDialog
 */
import { onMounted, onUnmounted, inject, computed } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import type { WSClient } from '@/runtime'
import StepTimeline from '@/components/StepTimeline.vue'
import BrowserPanel from '@/components/BrowserPanel.vue'
import ApprovalDialog from '@/components/ApprovalDialog.vue'

const props = defineProps<{ id: string }>()
const sessionId = computed(() => Number(props.id))

const sessionsStore = useSessionsStore()
const wsClient = inject<WSClient>('wsClient')!

const session = computed(() => sessionsStore.sessions.get(sessionId.value) ?? null)

onMounted(() => {
  // 如果还未初始化（直接访问 URL），先初始化
  if (!sessionsStore.sessions.has(sessionId.value)) {
    sessionsStore.initSession(sessionId.value)
  }
  sessionsStore.setActiveSession(sessionId.value)

  // 如果 WS 未连接，建立连接
  if (!wsClient.connected || wsClient.sessionIdValue !== sessionId.value) {
    wsClient.connect(sessionId.value)
  }
})

onUnmounted(() => {
  // 离开页面不断开 WS，保持数据接收
})

// ================================================================
// Status display helpers
// ================================================================

const statusClass = computed(() => {
  const s = session.value
  if (!s) return ''
  switch (s.status) {
    case 'running':
    case 'planning':
      return 'badge-running'
    case 'completed':
      return 'badge-success'
    case 'failed':
      return 'badge-failed'
    default:
      return 'badge-pending'
  }
})

const statusLabel = computed(() => {
  const s = session.value
  if (!s) return 'Unknown'
  const map: Record<string, string> = {
    pending: '等待中',
    planning: '规划中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[s.status] ?? s.status
})
</script>

<template>
  <div class="monitor">
    <!-- Status Bar -->
    <div class="status-bar">
      <div class="sb-left">
        <span class="badge" :class="statusClass">{{ statusLabel }}</span>
        <span class="sb-task">{{ session?.taskDescription ?? '加载中...' }}</span>
      </div>
      <div class="sb-right">
        <span class="sb-metric">
          Step {{ session?.totalStepsExecuted ?? 0 }}/{{ session?.planSteps.length || '?' }}
        </span>
        <span class="sb-metric">{{ session?.progressPct ?? 0 }}%</span>
        <span class="sb-metric" v-if="session?.currentUrl">
          {{ session.currentUrl }}
        </span>
      </div>
    </div>

    <div v-if="!session" class="monitor-empty">
      加载 Session 状态...
    </div>

    <template v-else>
      <!-- Progress Bar -->
      <div class="progress-bar">
        <div
          class="progress-fill"
          :class="{ 'is-error': session.status === 'failed' }"
          :style="{ width: session.progressPct + '%' }"
        />
      </div>

      <!-- Main Content -->
      <div class="monitor-main">
        <StepTimeline :session-id="sessionId" />

        <!-- Side Panel -->
        <aside class="monitor-side">
          <!-- Browser Panel (URL bar + screenshot) -->
          <BrowserPanel :session-id="sessionId" />

          <!-- Error -->
          <div v-if="session.errorMessage" class="card error-card">
            <h4>错误信息</h4>
            <pre>{{ session.errorMessage }}</pre>
          </div>

          <!-- Cost Summary -->
          <div v-if="session.status === 'completed' || session.status === 'failed'" class="card cost-card">
            <h4>执行摘要</h4>
            <div class="cc-row">
              <span>LLM 费用</span>
              <span>${{ session.llmCost }}</span>
            </div>
            <div class="cc-row">
              <span>总步数</span>
              <span>{{ session.totalStepsExecuted }}</span>
            </div>
            <div class="cc-row">
              <span>Tokens</span>
              <span>{{ session.tokensUsed.toLocaleString() }}</span>
            </div>
          </div>
        </aside>

        <!-- Approval Dialog (shown over everything when pending) -->
        <ApprovalDialog />
      </div>
    </template>
  </div>
</template>

<style scoped>
.monitor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 45px); /* subtract header height */
}

/* Status Bar */

.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}

.sb-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sb-task {
  font-size: 14px;
  font-weight: 500;
  max-width: 480px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.sb-metric {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-metric:last-child {
  color: var(--accent);
}

/* Progress Bar */

.progress-bar {
  height: 3px;
  background: var(--border);
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  transition: width .3s ease;
}

.progress-fill.is-error {
  background: var(--danger);
}

/* Main Content */

.monitor-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.monitor-side {
  width: 360px;
  padding: 16px;
  border-left: 1px solid var(--border);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.monitor-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
}

/* Side Cards */

.error-card h4,
.cost-card h4 {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
  margin-bottom: 8px;
}

.error-card pre {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--danger);
  white-space: pre-wrap;
}

.cost-card .cc-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-primary);
}

.cost-card .cc-row span:first-child {
  color: var(--text-secondary);
}
</style>
