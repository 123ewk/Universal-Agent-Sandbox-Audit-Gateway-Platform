<script setup lang="ts">
/**
 * ArtifactPanel — 产物面板
 *
 * 展示所有 Agent 执行产物（截图/日志/文本）
 * 数据源：artifact API + WebSocket sandbox.screenshot 事件
 */
import { computed } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { screenshotUrl } from '@/api/client'

const store = useSessionsStore()
const screenshots = computed(() => store.activeSession?.screenshots ?? [])
const recentScreenshots = computed(() => screenshots.value.slice(-6))
</script>

<template>
  <div class="artifact-panel">
    <h3 class="ap-title">产物</h3>

    <!-- Screenshots -->
    <div class="ap-section" v-if="screenshots.length > 0">
      <h4>截图 ({{ screenshots.length }})</h4>
      <div class="ap-thumbs">
        <div
          class="ap-thumb"
          v-for="(ss, idx) in recentScreenshots"
          :key="idx"
        >
          <img
            :src="screenshotUrl(store.activeSessionId ?? 0, ss.filename)"
            :alt="`Step ${ss.step_number}`"
            loading="lazy"
          />
          <span class="ap-thumb-label">Step {{ ss.step_number }}</span>
        </div>
      </div>
    </div>

    <div class="ap-empty" v-if="screenshots.length === 0">
      暂无产物，等待 Agent 执行...
    </div>
  </div>
</template>

<style scoped>
.artifact-panel { }
.ap-title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin: 0 0 8px; }
.ap-section { margin-bottom: 12px; }
.ap-section h4 { font-size: 11px; color: var(--text-secondary); margin: 0 0 6px; }
.ap-thumbs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.ap-thumb { position: relative; border-radius: 4px; overflow: hidden; border: 1px solid var(--border); }
.ap-thumb img { width: 100%; height: 50px; object-fit: cover; display: block; }
.ap-thumb-label { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,.7); color: #fff; font-size: 8px; padding: 1px 4px; }
.ap-empty { color: var(--text-secondary); font-size: 12px; text-align: center; padding: 16px 0; }
</style>
