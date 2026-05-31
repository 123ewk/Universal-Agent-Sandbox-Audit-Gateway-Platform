<script setup lang="ts">
/**
 * ResultPanel — 任务结果面板
 *
 * 展示结构化 TaskResult：
 *   - 执行摘要
 *   - 最终回答
 *   - 步骤详情（展开/折叠）
 *   - 费用统计
 *   - Browser 状态
 */
import { ref } from 'vue'
import type { TaskResultPayload } from '@/runtime/event-types'

const props = defineProps<{ result: TaskResultPayload }>()
const showSteps = ref(false)
</script>

<template>
  <div class="result-panel">
    <!-- Header -->
    <div class="rp-header">
      <span class="rp-icon">{{ result.total_steps > 0 ? '\u2713' : '\u2717' }}</span>
      <span class="rp-title">任务结果</span>
    </div>

    <!-- Summary -->
    <div class="rp-section">
      <h4>执行摘要</h4>
      <p class="rp-summary">{{ result.summary }}</p>
    </div>

    <!-- Final Answer -->
    <div class="rp-section" v-if="result.final_answer">
      <h4>最终回答</h4>
      <p class="rp-answer">{{ result.final_answer }}</p>
    </div>

    <!-- Stats -->
    <div class="rp-stats">
      <div class="rp-stat">
        <span class="rp-stat-value">{{ result.total_steps }}</span>
        <span class="rp-stat-label">步骤</span>
      </div>
      <div class="rp-stat">
        <span class="rp-stat-value">{{ result.total_tokens.toLocaleString() }}</span>
        <span class="rp-stat-label">Tokens</span>
      </div>
      <div class="rp-stat">
        <span class="rp-stat-value">${{ result.total_cost }}</span>
        <span class="rp-stat-label">费用</span>
      </div>
      <div class="rp-stat">
        <span class="rp-stat-value" :class="result.browser_active ? 'rp-green' : ''">
          {{ result.browser_active ? '活跃' : '已关闭' }}
        </span>
        <span class="rp-stat-label">浏览器</span>
      </div>
    </div>

    <!-- Steps (collapsible) -->
    <div class="rp-section" v-if="result.steps.length > 0">
      <div class="rp-section-header" @click="showSteps = !showSteps">
        <h4>步骤详情 ({{ result.steps.length }})</h4>
        <span class="rp-toggle">{{ showSteps ? '\u25B4' : '\u25BE' }}</span>
      </div>
      <div v-if="showSteps" class="rp-step-list">
        <div
          v-for="s in result.steps"
          :key="s.step"
          class="rp-step-item"
        >
          <span class="rp-step-icon">{{ s.success ? '\u2713' : '\u2717' }}</span>
          <span class="rp-step-name">{{ s.skill }}</span>
          <span class="rp-step-time">{{ s.time_ms }}ms</span>
          <span class="rp-step-tokens">{{ s.tokens }}tok</span>
          <span class="rp-step-cost">${{ s.cost }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-panel {
  padding: 12px;
  background: var(--bg-secondary, #1a1a2e);
  border-radius: 8px;
  border: 1px solid var(--border, #2a2a4a);
}

.rp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.rp-icon {
  font-size: 18px;
  color: var(--success, #22c55e);
}

.rp-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #e0e0e0);
}

.rp-section {
  margin-bottom: 12px;
}

.rp-section h4 {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary, #888);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 4px;
}

.rp-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.rp-toggle {
  font-size: 14px;
  color: var(--text-secondary, #888);
}

.rp-summary,
.rp-answer {
  font-size: 12px;
  color: var(--text-primary, #e0e0e0);
  line-height: 1.5;
  margin: 0;
  white-space: pre-wrap;
}

.rp-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.rp-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px;
  background: var(--bg-primary, #12121e);
  border-radius: 6px;
}

.rp-stat-value {
  font-size: 16px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: var(--text-primary, #e0e0e0);
}

.rp-stat-value.rp-green {
  color: var(--success, #22c55e);
}

.rp-stat-label {
  font-size: 9px;
  color: var(--text-secondary, #888);
  text-transform: uppercase;
  margin-top: 2px;
}

.rp-step-list {
  max-height: 200px;
  overflow-y: auto;
}

.rp-step-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  border-bottom: 1px solid var(--border, #2a2a4a);
}

.rp-step-item:last-child {
  border-bottom: none;
}

.rp-step-icon { width: 14px; }
.rp-step-name { flex: 1; color: var(--text-primary, #e0e0e0); }
.rp-step-time { color: var(--text-secondary, #888); }
.rp-step-tokens { color: var(--accent, #58a6ff); }
.rp-step-cost { color: var(--warning, #f59e0b); }
</style>
