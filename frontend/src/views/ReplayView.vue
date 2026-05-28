<script setup lang="ts">
/**
 * ReplayView — 任务回放
 *
 * 已完成的 Agent Session 的回放视图。
 * 从截图文件名重建步骤序列，支持逐步浏览和自动播放。
 *
 * 设计原则（用户方案）：
 *   EventLog + Snapshot = 事件时间线重放，不是截图轮播
 *   每步截图作为该步的"快照"，按步骤号顺序回放
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getTaskStatus, getScreenshotList, screenshotUrl, type TaskStatus, type ScreenshotInfo } from '@/api/client'
import AuditPanel from '@/components/AuditPanel.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()
const sessionId = computed(() => Number(props.id))

// State
const task = ref<TaskStatus | null>(null)
const screenshots = ref<ScreenshotInfo[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

// Playback
const currentIndex = ref(0)
const autoPlay = ref(false)
let playTimer: ReturnType<typeof setInterval> | null = null

// ================================================================
// Load data
// ================================================================

onMounted(async () => {
  try {
    const [t, s] = await Promise.all([
      getTaskStatus(sessionId.value),
      getScreenshotList(sessionId.value),
    ])
    task.value = t
    screenshots.value = s.screenshots
    if (s.screenshots.length > 0) {
      currentIndex.value = 0
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
})

// ================================================================
// Computed
// ================================================================

const currentScreenshot = computed<ScreenshotInfo | null>(() => {
  return screenshots.value[currentIndex.value] ?? null
})

const currentScreenshotUrl = computed(() => {
  const s = currentScreenshot.value
  if (!s) return ''
  return screenshotUrl(sessionId.value, s.filename)
})

const totalSteps = computed(() => screenshots.value.length)
const progressPct = computed(() => {
  if (totalSteps.value === 0) return 0
  return ((currentIndex.value + 1) / totalSteps.value) * 100
})

// Group screenshots by step number for the timeline
const stepGroups = computed(() => {
  const groups = new Map<number, ScreenshotInfo[]>()
  for (const s of screenshots.value) {
    const existing = groups.get(s.step_number) || []
    existing.push(s)
    groups.set(s.step_number, existing)
  }
  return groups
})

const uniqueSteps = computed(() => {
  return Array.from(stepGroups.value.entries())
    .sort(([a], [b]) => a - b)
})

// ================================================================
// Navigation
// ================================================================

function goTo(index: number): void {
  if (index >= 0 && index < totalSteps.value) {
    currentIndex.value = index
  }
}

function goPrev(): void {
  goTo(currentIndex.value - 1)
}

function goNext(): void {
  goTo(currentIndex.value + 1)
}

function toggleAutoPlay(): void {
  autoPlay.value = !autoPlay.value
  if (autoPlay.value) {
    playTimer = setInterval(() => {
      if (currentIndex.value < totalSteps.value - 1) {
        currentIndex.value++
      } else {
        stopAutoPlay()
      }
    }, 1500)
  } else {
    stopAutoPlay()
  }
}

function stopAutoPlay(): void {
  autoPlay.value = false
  if (playTimer) {
    clearInterval(playTimer)
    playTimer = null
  }
}

watch(() => props.id, () => {
  stopAutoPlay()
})

// ================================================================
// Helpers
// ================================================================

function statusBadge(status: string): string {
  const map: Record<string, string> = {
    success: 'badge-success',
    completed: 'badge-success',
    failed: 'badge-failed',
    cancelled: 'badge-pending',
  }
  return map[status] ?? 'badge-pending'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    success: '已完成',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] ?? status
}

function actionLabel(action: string): string {
  const map: Record<string, string> = {
    goto: '导航',
    click: '点击',
    type: '输入',
    screenshot: '截图',
    extract_text: '提取文本',
  }
  return map[action] || action
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  return (bytes / 1024).toFixed(1) + ' KB'
}

// Keyboard shortcuts
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'ArrowLeft') goPrev()
  if (e.key === 'ArrowRight') goNext()
  if (e.key === ' ') { e.preventDefault(); toggleAutoPlay() }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  stopAutoPlay()
})
</script>

<template>
  <div class="replay">
    <!-- Loading / Error -->
    <div v-if="loading" class="rp-empty">加载中...</div>
    <div v-else-if="error" class="rp-empty rp-error">{{ error }}</div>

    <template v-else-if="task">
      <!-- Status Bar -->
      <div class="rp-status-bar">
        <div class="rp-sb-left">
          <router-link to="/history" class="rp-back">&larr; 历史记录</router-link>
          <span class="badge" :class="statusBadge(task.status)">
            {{ statusLabel(task.status) }}
          </span>
          <span class="rp-task-desc">{{ task.task_description }}</span>
        </div>
        <div class="rp-sb-right">
          <span class="rp-metric">{{ task.total_steps_executed }} steps</span>
          <span class="rp-metric">${{ task.llm_cost }}</span>
        </div>
      </div>

      <!-- Progress Bar -->
      <div class="rp-progress-bar">
        <div class="rp-progress-fill" :style="{ width: progressPct + '%' }" />
      </div>

      <!-- Main Content -->
      <div class="rp-main">
        <!-- Step Timeline -->
        <aside class="rp-timeline">
          <div class="rp-timeline-header">
            Steps ({{ totalSteps }} 张截图)
          </div>

          <div v-if="totalSteps === 0" class="rp-tl-empty">
            该任务没有截图记录
          </div>

          <div
            v-for="(step, idx) in screenshots"
            :key="idx"
            class="rp-step"
            :class="{ active: idx === currentIndex }"
            @click="goTo(idx)"
          >
            <span class="rp-step-num">{{ step.step_number }}</span>
            <span class="rp-step-action">{{ actionLabel(step.action) }}</span>
            <span class="rp-step-size">{{ formatSize(step.size_bytes) }}</span>
          </div>

          <!-- Audit Panel at bottom of sidebar -->
          <div class="rp-audit-section">
            <AuditPanel :session-id="sessionId" />
          </div>
        </aside>

        <!-- Screenshot Viewer -->
        <div class="rp-viewer">
          <!-- Controls -->
          <div class="rp-controls">
            <button class="btn" :disabled="currentIndex === 0" @click="goPrev">
              &larr; 上一步
            </button>
            <span class="rp-ctrl-info">
              {{ currentIndex + 1 }} / {{ totalSteps }}
              <template v-if="currentScreenshot">
                — Step {{ currentScreenshot.step_number }}
                {{ actionLabel(currentScreenshot.action) }}
              </template>
            </span>
            <button
              class="btn"
              :disabled="currentIndex >= totalSteps - 1"
              @click="goNext"
            >
              下一步 &rarr;
            </button>
            <button class="btn" @click="toggleAutoPlay">
              {{ autoPlay ? '⏸ 暂停' : '▶ 播放' }}
            </button>
          </div>

          <!-- Screenshot -->
          <div class="rp-screenshot-area">
            <img
              v-if="currentScreenshotUrl"
              :key="currentIndex"
              :src="currentScreenshotUrl"
              class="rp-screenshot-img"
              :alt="currentScreenshot?.filename ?? ''"
            />
            <div v-else class="rp-no-screenshot">
              无截图
            </div>
          </div>

          <!-- Metadata -->
          <div v-if="currentScreenshot" class="rp-meta">
            <span>{{ currentScreenshot.filename }}</span>
            <span>{{ formatSize(currentScreenshot.size_bytes) }}</span>
          </div>
        </div>
      </div>

      <!-- Keyboard hint -->
      <div class="rp-kbd-hint">
        &larr; &rarr; 切换步骤 &nbsp;|&nbsp; Space 播放/暂停
      </div>
    </template>
  </div>
</template>

<style scoped>
.replay {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 45px);
}

/* Status Bar */

.rp-status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}

.rp-sb-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rp-back {
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}

.rp-back:hover {
  text-decoration: underline;
}

.rp-task-desc {
  font-size: 14px;
  font-weight: 500;
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rp-sb-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.rp-metric {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* Progress Bar */

.rp-progress-bar {
  height: 3px;
  background: var(--border);
}

.rp-progress-fill {
  height: 100%;
  background: var(--success);
  transition: width .2s;
}

/* Main Content */

.rp-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Timeline Sidebar */

.rp-timeline {
  width: 240px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: var(--bg-secondary);
}

.rp-timeline-header {
  padding: 12px 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
  border-bottom: 1px solid var(--border);
}

.rp-tl-empty {
  padding: 24px 16px;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
}

.rp-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 12px;
  cursor: pointer;
  border-bottom: 1px solid rgba(48, 54, 61, .4);
  transition: background .1s;
}

.rp-step:hover {
  background: rgba(88, 166, 255, .05);
}

.rp-step.active {
  background: rgba(88, 166, 255, .1);
  border-left: 2px solid var(--accent);
}

.rp-step-num {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  background: rgba(48, 54, 61, .4);
  border-radius: 3px;
  flex-shrink: 0;
}

.rp-step.active .rp-step-num {
  color: var(--accent);
  background: rgba(88, 166, 255, .15);
}

.rp-step-action {
  flex: 1;
  font-family: var(--font-mono);
  color: var(--text-primary);
}

.rp-step-size {
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.rp-audit-section {
  border-top: 1px solid var(--border);
  padding: 12px 16px;
}

/* Viewer */

.rp-viewer {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

/* Controls */

.rp-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}

.rp-ctrl-info {
  flex: 1;
  text-align: center;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* Screenshot Area */

.rp-screenshot-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  overflow: hidden;
}

.rp-screenshot-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: var(--radius);
  border: 1px solid var(--border);
}

.rp-no-screenshot {
  color: var(--text-muted);
  font-size: 14px;
}

/* Meta */

.rp-meta {
  display: flex;
  justify-content: space-between;
  padding: 8px 20px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
}

/* Keyboard hint */

.rp-kbd-hint {
  padding: 6px;
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
}

/* Empty states */

.rp-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 14px;
}

.rp-error {
  color: var(--danger);
}
</style>
