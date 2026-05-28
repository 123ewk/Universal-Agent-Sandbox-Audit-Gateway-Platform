<script setup lang="ts">
/**
 * StepTimeline — 实时步骤执行时间线
 *
 * 监听 sessions store 的 executionHistory，
 * 滚动显示最新步骤，实时更新状态。
 *
 * 每行显示：步骤号 | Skill 名 | 状态 | 耗时 | 错误
 */
import { computed, ref, watch, nextTick } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import type { StepRecord } from '@/stores/sessions'

const props = defineProps<{ sessionId: number }>()
const sessionsStore = useSessionsStore()
const scrollContainer = ref<HTMLElement | null>(null)

const steps = computed<StepRecord[]>(() => {
  const s = sessionsStore.sessions.get(props.sessionId)
  return s?.executionHistory ?? []
})

const planSteps = computed(() => {
  const s = sessionsStore.sessions.get(props.sessionId)
  return s?.planSteps ?? []
})

// 自动滚动到最新步骤
watch(
  () => steps.value.filter((s) => s.status !== 'pending').length,
  async () => {
    await nextTick()
    if (scrollContainer.value) {
      scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
    }
  },
)

// ================================================================
// Display helpers
// ================================================================

function statusIcon(status: string): string {
  switch (status) {
    case 'success': return '\u2713'
    case 'failed': return '\u2717'
    case 'running': return '\u25B6'
    default: return '\u25CB'
  }
}

function statusClass(status: string): string {
  switch (status) {
    case 'success': return 'st-success'
    case 'failed': return 'st-failed'
    case 'running': return 'st-running'
    default: return 'st-pending'
  }
}

function formatTime(ms: number): string {
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  return (ms / 60000).toFixed(1) + 'm'
}
</script>

<template>
  <div class="timeline" ref="scrollContainer">
    <!-- Plan Summary Header -->
    <div v-if="planSteps.length > 0" class="tl-plan-summary">
      计划步骤：{{ planSteps.map((s) => s.skill_name || s.description).join(' → ') }}
    </div>

    <!-- Step List -->
    <div class="tl-steps">
      <div
        v-for="step in steps"
        :key="step.step_number"
        class="tl-step"
        :class="statusClass(step.status)"
      >
        <!-- Step Number -->
        <span class="tl-num">{{ step.step_number }}</span>

        <!-- Status Icon -->
        <span class="tl-icon">{{ statusIcon(step.status) }}</span>

        <!-- Content -->
        <div class="tl-content">
          <span class="tl-skill">{{ step.skill_name || step.description }}</span>
          <span v-if="step.error" class="tl-error">{{ step.error }}</span>
        </div>

        <!-- Time -->
        <span v-if="step.execution_time_ms > 0" class="tl-time">
          {{ formatTime(step.execution_time_ms) }}
        </span>
      </div>

      <!-- Empty State -->
      <div v-if="steps.length === 0" class="tl-empty">
        {{ planSteps.length > 0 ? '等待执行...' : '等待计划生成...' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.timeline {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  font-family: var(--font-mono);
  font-size: 13px;
}

/* Plan Summary */

.tl-plan-summary {
  padding: 8px 12px;
  margin-bottom: 16px;
  background: rgba(88, 166, 255, .06);
  border: 1px solid rgba(88, 166, 255, .15);
  border-radius: var(--radius);
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

/* Steps */

.tl-steps {
  display: flex;
  flex-direction: column;
}

.tl-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(48, 54, 61, .4);
  transition: background .2s;
}

.tl-step:last-child {
  border-bottom: none;
}

.tl-num {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  background: rgba(48, 54, 61, .3);
  border-radius: 4px;
}

.tl-icon {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  color: var(--text-muted);
}

.st-success .tl-icon { color: var(--success); }
.st-failed .tl-icon { color: var(--danger); }
.st-running .tl-icon { color: var(--accent); }

.tl-content {
  flex: 1;
  min-width: 0;
}

.tl-skill {
  display: block;
  color: var(--text-primary);
}

.st-running .tl-skill {
  color: var(--accent);
}

.st-failed .tl-skill {
  color: var(--danger);
}

.tl-error {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  color: var(--danger);
  word-break: break-all;
}

.tl-time {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-muted);
  min-width: 48px;
  text-align: right;
}

.tl-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--text-muted);
}
</style>
