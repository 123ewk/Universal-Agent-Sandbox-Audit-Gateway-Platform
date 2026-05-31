<script setup lang="ts">
/**
 * ThoughtTimeline — Agent 思考过程时间线
 *
 * 视觉：左侧竖线 + 节点气泡
 * 数据：sessionsStore.thoughtHistory
 */
import { computed, nextTick, watch, ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'

const store = useSessionsStore()
const container = ref<HTMLElement | null>(null)

const thoughts = computed(() => store.activeSession?.thoughtHistory ?? [])

// 自动滚动到底部
watch(() => thoughts.value.length, async () => {
  await nextTick()
  if (container.value) {
    container.value.scrollTop = container.value.scrollHeight
  }
})
</script>

<template>
  <div class="timeline" ref="container">
    <div class="timeline-node" v-for="(t, idx) in thoughts" :key="idx">
      <div class="tl-dot" :class="{ 'tl-dot-latest': idx === thoughts.length - 1 }" />
      <div class="tl-content">
        <div class="tl-time">Step {{ t.step_number || idx + 1 }}</div>
        <div class="tl-thought">{{ t.thought }}</div>
        <div class="tl-meta">
          <span class="tl-confidence">置信度 {{ Math.round(t.confidence * 100) }}%</span>
          <span class="tl-intent" v-if="t.intent">{{ t.intent }}</span>
        </div>
      </div>
    </div>
    <div class="tl-empty" v-if="thoughts.length === 0">
      等待 Agent 思考...
    </div>
  </div>
</template>

<style scoped>
.timeline { padding: 0 0 0 20px; border-left: 2px solid var(--border); overflow-y: auto; flex: 1; }
.timeline-node { position: relative; padding: 8px 0 8px 16px; }
.tl-dot { position: absolute; left: -27px; top: 14px; width: 10px; height: 10px; border-radius: 50%; background: var(--text-secondary); }
.tl-dot-latest { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
.tl-content { font-size: 12px; }
.tl-time { font-size: 10px; color: var(--text-secondary); margin-bottom: 2px; }
.tl-thought { color: var(--text-primary); line-height: 1.5; }
.tl-meta { display: flex; gap: 8px; margin-top: 4px; font-size: 10px; }
.tl-confidence { color: var(--accent); }
.tl-intent { color: var(--warning); }
.tl-empty { color: var(--text-secondary); font-size: 12px; text-align: center; padding: 24px 0; }
</style>
