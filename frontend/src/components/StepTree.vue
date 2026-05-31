<script setup lang="ts">
/**
 * StepTree — 步骤树组件（展开子步骤）
 *
 * 替换当前 StepTimeline，展示步骤的父子关系：
 *   Plan Step → thought → tool_call → result → observation
 */
import { watch, nextTick, ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'

const store = useSessionsStore()
const container = ref<HTMLElement | null>(null)

const steps = store.recentSteps ?? []
const planSteps = store.activeSession?.planSteps ?? []

watch(() => steps.length, async () => {
  await nextTick()
  if (container.value) container.value.scrollTop = container.value.scrollHeight
})
</script>

<template>
  <div class="step-tree" ref="container">
    <div
      class="st-item"
      v-for="(step, idx) in steps"
      :key="idx"
      :class="`st-${step.status}`"
    >
      <div class="st-header">
        <span class="st-icon">
          {{ step.status === 'success' ? '\u2713' : step.status === 'failed' ? '\u2717' : step.status === 'running' ? '\u25B6' : '\u25CB' }}
        </span>
        <span class="st-desc">{{ step.description }}</span>
        <span class="st-time">{{ step.execution_time_ms }}ms</span>
      </div>
      <div class="st-detail" v-if="step.status !== 'pending'">
        <div class="st-skill">Tool: {{ step.skill_name }}</div>
        <div class="st-error" v-if="step.error">{{ step.error }}</div>
      </div>
    </div>
    <div class="st-empty" v-if="steps.length === 0">
      等待执行...
    </div>
  </div>
</template>

<style scoped>
.step-tree { overflow-y: auto; flex: 1; }
.st-item { padding: 8px 0; border-bottom: 1px solid var(--border); }
.st-item:last-child { border-bottom: none; }
.st-header { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.st-icon { font-size: 10px; width: 16px; text-align: center; flex-shrink: 0; }
.st-success .st-icon { color: var(--success); }
.st-failed .st-icon { color: var(--danger); }
.st-running .st-icon { color: var(--accent); animation: pulse 1s infinite; }
.st-desc { flex: 1; color: var(--text-primary); }
.st-time { font-size: 10px; color: var(--text-secondary); white-space: nowrap; }
.st-detail { margin: 4px 0 0 24px; font-size: 10px; color: var(--text-secondary); }
.st-skill { font-family: monospace; }
.st-error { color: var(--danger); margin-top: 2px; }
.st-empty { color: var(--text-secondary); font-size: 12px; text-align: center; padding: 24px 0; }
</style>
