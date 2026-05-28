<script setup lang="ts">
/**
 * AuditPanel — 审计面板
 *
 * 展示 Session 的审计与风险检测结果。
 * 集成到 ReplayView / MonitorView 侧栏。
 *
 * 数据来源：
 *   - 审批记录（approvalsStore — 来自 WS approval.* 事件）
 *   - 风险检测结果（来自 audit.* 事件）
 *   - 任务执行摘要
 */
import { computed } from 'vue'
import { useApprovalsStore } from '@/stores/approvals'

const props = defineProps<{ sessionId: number }>()
const approvalsStore = useApprovalsStore()

// All approval items for this session
const sessionApprovals = computed(() => {
  return Array.from(approvalsStore.items.values())
    .filter((a) => a.sessionId === props.sessionId)
})

const approved = computed(() => sessionApprovals.value.filter((a) => a.status === 'approved'))
const denied = computed(() => sessionApprovals.value.filter((a) => a.status === 'denied'))
const pendingCount = computed(() => approvalsStore.pendingForSession(props.sessionId).length)

function approvalStatusIcon(status: string): string {
  switch (status) {
    case 'approved': return '\u2713'
    case 'denied': return '\u2717'
    case 'timeout': return '\u23F1'
    default: return '\u25CB'
  }
}

function riskClass(score: number): string {
  if (score >= 80) return 'ap-danger'
  if (score >= 50) return 'ap-warning'
  return 'ap-info'
}
</script>

<template>
  <div class="audit-panel">
    <h4 class="ap-title">审计记录</h4>

    <!-- Empty -->
    <div v-if="sessionApprovals.length === 0" class="ap-empty">
      暂无审计事件
    </div>

    <!-- Summary -->
    <div v-else class="ap-summary">
      <div class="ap-stat">
        <span class="ap-stat-value">{{ sessionApprovals.length }}</span>
        <span class="ap-stat-label">总事件</span>
      </div>
      <div class="ap-stat ap-stat-approved">
        <span class="ap-stat-value">{{ approved.length }}</span>
        <span class="ap-stat-label">已批准</span>
      </div>
      <div class="ap-stat ap-stat-denied">
        <span class="ap-stat-value">{{ denied.length }}</span>
        <span class="ap-stat-label">已拒绝</span>
      </div>
      <div v-if="pendingCount > 0" class="ap-stat ap-stat-pending">
        <span class="ap-stat-value">{{ pendingCount }}</span>
        <span class="ap-stat-label">待审批</span>
      </div>
    </div>

    <!-- Event List -->
    <div v-if="sessionApprovals.length > 0" class="ap-list">
      <div
        v-for="a in sessionApprovals"
        :key="a.approvalId"
        class="ap-item"
      >
        <span class="ap-item-icon">{{ approvalStatusIcon(a.status) }}</span>
        <div class="ap-item-content">
          <span class="ap-item-skill">{{ a.skillName }}</span>
          <span class="ap-item-reasons" v-if="a.riskReasons.length > 0">
            {{ a.riskReasons.join('; ') }}
          </span>
        </div>
        <span class="ap-item-risk" :class="riskClass(a.riskScore)">
          {{ a.riskScore }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.audit-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ap-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
}

.ap-empty {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  padding: 16px 0;
}

/* Summary Stats */

.ap-summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.ap-stat {
  padding: 8px;
  background: var(--bg-primary);
  border-radius: var(--radius);
  text-align: center;
}

.ap-stat-value {
  display: block;
  font-size: 18px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--text-primary);
}

.ap-stat-label {
  font-size: 10px;
  color: var(--text-muted);
}

.ap-stat-approved .ap-stat-value { color: var(--success); }
.ap-stat-denied .ap-stat-value { color: var(--danger); }
.ap-stat-pending .ap-stat-value { color: var(--warning); }

/* Event List */

.ap-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 300px;
  overflow-y: auto;
}

.ap-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: var(--bg-primary);
  border-radius: 4px;
}

.ap-item-icon {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  font-size: 12px;
  color: var(--text-muted);
}

.ap-item-content {
  flex: 1;
  min-width: 0;
}

.ap-item-skill {
  display: block;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-primary);
}

.ap-item-reasons {
  display: block;
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ap-item-risk {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono);
  padding: 2px 6px;
  border-radius: 3px;
  background: rgba(88, 166, 255, .1);
  color: var(--accent);
}

.ap-item-risk.ap-warning {
  background: rgba(210, 153, 34, .1);
  color: var(--warning);
}

.ap-item-risk.ap-danger {
  background: rgba(248, 81, 73, .1);
  color: var(--danger);
}
</style>
