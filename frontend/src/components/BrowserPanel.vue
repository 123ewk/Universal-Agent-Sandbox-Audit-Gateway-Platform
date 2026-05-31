<script setup lang="ts">
/**
 * BrowserPanel — 浏览器视图面板
 *
 * 展示 Agent 操作的浏览器实时状态：
 *   - URL 地址栏（只读）
 *   - 页面标题
 *   - 最新截图（WS 推送路径 → HTTP 加载图片）
 *   - 截图历史缩略图列表
 *   - 点击截图可放大查看
 */
import { computed, watch, ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import type { ScreenshotPayload } from '@/runtime/event-types'
import ScreenshotModal from './ScreenshotModal.vue'

const props = defineProps<{ sessionId: number }>()
const sessionsStore = useSessionsStore()

const session = computed(() => sessionsStore.sessions.get(props.sessionId) ?? null)

const currentUrl = computed(() => session.value?.currentUrl ?? '')
const pageTitle = computed(() => session.value?.pageTitle ?? '')
const screenshots = computed(() => session.value?.screenshots ?? [])
const latestScreenshot = computed<ScreenshotPayload | null>(() => {
  const list = screenshots.value
  return list.length > 0 ? list[list.length - 1] : null
})

const selectedScreenshot = ref<number | null>(null)
const showModal = ref(false)

function screenshotUrlFor(s: ScreenshotPayload): string {
  return `/api/screenshots/${s.filename}?session_id=${props.sessionId}`
}

function currentSrc(): string {
  if (selectedScreenshot.value !== null) {
    const s = screenshots.value[selectedScreenshot.value]
    if (s) return screenshotUrlFor(s)
  }
  const s = latestScreenshot.value
  return s ? screenshotUrlFor(s) : ''
}

function currentFilename(): string {
  if (selectedScreenshot.value !== null) {
    const s = screenshots.value[selectedScreenshot.value]
    if (s) return s.filename
  }
  const s = latestScreenshot.value
  return s ? s.filename : ''
}

function selectScreenshot(index: number): void {
  selectedScreenshot.value = index
}

function openModal(): void {
  if (screenshots.value.length > 0) showModal.value = true
}

function modalNext(): void {
  if (selectedScreenshot.value === null) return
  const next = selectedScreenshot.value + 1
  if (next < screenshots.value.length) selectedScreenshot.value = next
}

function modalPrev(): void {
  if (selectedScreenshot.value === null) return
  const prev = selectedScreenshot.value - 1
  if (prev >= 0) selectedScreenshot.value = prev
}

const displayedUrl = computed(() => {
  if (selectedScreenshot.value !== null) {
    const s = screenshots.value[selectedScreenshot.value]
    return s ? `Step ${s.step_number}` : ''
  }
  return ''
})

// Auto-select latest screenshot
watch(
  () => screenshots.value.length,
  (len) => {
    if (len > 0) selectedScreenshot.value = len - 1
  },
)
</script>

<template>
  <div class="browser-panel">
    <!-- Status Bar -->
    <div class="bp-status-bar">
      <span class="bp-status-dot" :class="{ active: screenshots.length > 0 }"></span>
      <span class="bp-status-text">{{ screenshots.length > 0 ? 'Browser ACTIVE' : '等待浏览器...' }}</span>
    </div>

    <!-- URL Bar -->
    <div class="bp-url-bar">
      <div class="bp-url-icon">&#x1F310;</div>
      <div class="bp-url-text">{{ currentUrl || '暂无页面' }}</div>
    </div>

    <!-- Page Title -->
    <div v-if="pageTitle" class="bp-title">{{ pageTitle }}</div>

    <!-- Screenshot Display -->
    <div class="bp-screenshot-area" @click="openModal" role="button" tabindex="0" title="点击放大查看">
      <template v-if="currentSrc()">
        <img
          :src="currentSrc()"
          :alt="displayedUrl || pageTitle"
          class="bp-screenshot-img"
        />
        <div class="bp-overlay-hint">点击查看</div>
      </template>
      <div v-else class="bp-screenshot-empty">
        <p>等待截图...</p>
        <span>Agent 执行浏览器操作后将自动截取</span>
      </div>
    </div>

    <!-- Thumbnail List -->
    <div v-if="screenshots.length > 1" class="bp-thumbnails">
      <div
        v-for="(s, i) in screenshots"
        :key="i"
        class="bp-thumb"
        :class="{ active: selectedScreenshot === i }"
        @click="selectScreenshot(i)"
      >
        <span class="bp-thumb-label">{{ s.step_number }}</span>
      </div>
    </div>

    <!-- Info Footer -->
    <div v-if="latestScreenshot" class="bp-footer">
      <span>Step {{ latestScreenshot.step_number }}</span>
      <span>{{ (latestScreenshot.size_bytes / 1024).toFixed(1) }} KB</span>
      <button class="bp-view-btn" @click="openModal">查看</button>
    </div>

    <!-- Screenshot Modal -->
    <ScreenshotModal
      :src="currentSrc()"
      :filename="currentFilename()"
      :visible="showModal"
      @close="showModal = false"
      @next="modalNext"
      @prev="modalPrev"
    />
  </div>
</template>

<style scoped>
.browser-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* URL Bar */

/* Status Bar */
.bp-status-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
}

.bp-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #555;
  transition: background 0.3s;
}

.bp-status-dot.active {
  background: var(--success, #22c55e);
  box-shadow: 0 0 6px rgba(34, 197, 94, 0.5);
}

.bp-status-text {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.bp-url-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.bp-url-icon {
  flex-shrink: 0;
  font-size: 12px;
}

.bp-url-text {
  flex: 1;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--accent);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bp-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Screenshot Area */

.bp-screenshot-area {
  position: relative;
  width: 100%;
  aspect-ratio: 4 / 3;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.bp-screenshot-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  cursor: pointer;
}

.bp-overlay-hint {
  position: absolute;
  bottom: 8px;
  right: 8px;
  padding: 3px 10px;
  background: rgba(0, 0, 0, 0.6);
  color: rgba(255, 255, 255, 0.8);
  font-size: 10px;
  border-radius: 4px;
  opacity: 0;
  transition: opacity 0.2s;
  pointer-events: none;
}

.bp-screenshot-area:hover .bp-overlay-hint {
  opacity: 1;
}

.bp-screenshot-empty {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  gap: 4px;
}

.bp-screenshot-empty p {
  font-size: 13px;
}

.bp-screenshot-empty span {
  font-size: 11px;
}

/* Thumbnails */

.bp-thumbnails {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 4px 0;
}

.bp-thumb {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
  transition: border-color .15s;
}

.bp-thumb:hover {
  border-color: var(--accent);
}

.bp-thumb.active {
  border-color: var(--accent);
  background: rgba(88, 166, 255, .1);
}

.bp-thumb-label {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* Footer */

.bp-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}

.bp-view-btn {
  font-size: 10px;
  padding: 2px 8px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.bp-view-btn:hover {
  opacity: 0.85;
}
</style>
