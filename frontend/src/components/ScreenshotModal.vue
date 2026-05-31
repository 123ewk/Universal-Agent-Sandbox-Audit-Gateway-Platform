<script setup lang="ts">
/**
 * ScreenshotModal — 截图查看器 Modal
 *
 * 支持：
 *   - 放大/缩小（滚轮 + 按钮）
 *   - 拖动查看
 *   - 全屏模式
 *   - 下载原图
 *   - 键盘导航（← → 切换截图）
 */
import { ref, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps<{
  src: string
  filename: string
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  next: []
  prev: []
}>()

const scale = ref(1)
const position = ref({ x: 0, y: 0 })
const isDragging = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const isFullscreen = ref(false)

watch(() => props.visible, (val) => {
  if (val) {
    scale.value = 1
    position.value = { x: 0, y: 0 }
  }
})

function zoomIn(): void {
  scale.value = Math.min(scale.value * 1.5, 10)
}

function zoomOut(): void {
  scale.value = Math.max(scale.value / 1.5, 0.1)
}

function resetZoom(): void {
  scale.value = 1
  position.value = { x: 0, y: 0 }
}

function handleWheel(e: WheelEvent): void {
  e.preventDefault()
  if (e.deltaY < 0) zoomIn()
  else zoomOut()
}

function handleMouseDown(e: MouseEvent): void {
  if (e.button !== 0) return
  isDragging.value = true
  dragStart.value = { x: e.clientX - position.value.x, y: e.clientY - position.value.y }
}

function handleMouseMove(e: MouseEvent): void {
  if (!isDragging.value) return
  position.value.x = e.clientX - dragStart.value.x
  position.value.y = e.clientY - dragStart.value.y
}

function handleMouseUp(): void {
  isDragging.value = false
}

function toggleFullscreen(): void {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen()
    isFullscreen.value = true
  } else {
    document.exitFullscreen()
    isFullscreen.value = false
  }
}

function handleDownload(): void {
  const a = document.createElement('a')
  a.href = props.src
  a.download = props.filename || 'screenshot.png'
  a.click()
}

function handleKeydown(e: KeyboardEvent): void {
  if (!props.visible) return
  switch (e.key) {
    case 'Escape':
      emit('close')
      break
    case 'ArrowRight':
      emit('next')
      break
    case 'ArrowLeft':
      emit('prev')
      break
    case '+':
      zoomIn()
      break
    case '-':
      zoomOut()
      break
    case '0':
      resetZoom()
      break
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="screenshot-modal-overlay" @click.self="emit('close')">
      <!-- Toolbar -->
      <div class="sm-toolbar">
        <div class="sm-toolbar-left">
          <span class="sm-filename">{{ filename }}</span>
        </div>
        <div class="sm-toolbar-center">
          <button class="sm-btn" @click="zoomOut" title="缩小 ( - )">−</button>
          <span class="sm-zoom-level">{{ Math.round(scale * 100) }}%</span>
          <button class="sm-btn" @click="zoomIn" title="放大 ( + )">+</button>
          <button class="sm-btn" @click="resetZoom" title="重置 ( 0 )">⟲</button>
        </div>
        <div class="sm-toolbar-right">
          <button class="sm-btn" @click="emit('prev')" title="上一张 ( ← )">◀</button>
          <button class="sm-btn" @click="emit('next')" title="下一张 ( → )">▶</button>
          <button class="sm-btn" @click="handleDownload" title="下载">⬇</button>
          <button class="sm-btn" @click="toggleFullscreen" title="全屏">⛶</button>
          <button class="sm-btn sm-btn-close" @click="emit('close')" title="关闭 ( Esc )">✕</button>
        </div>
      </div>

      <!-- Image Container -->
      <div
        class="sm-image-container"
        @wheel.prevent="handleWheel"
        @mousedown="handleMouseDown"
        @mousemove="handleMouseMove"
        @mouseup="handleMouseUp"
        @mouseleave="handleMouseUp"
        :class="{ dragging: isDragging }"
      >
        <img
          :src="src"
          :alt="filename"
          class="sm-image"
          :style="{
            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
            cursor: isDragging ? 'grabbing' : 'grab',
          }"
        />
      </div>

      <!-- Hint -->
      <div class="sm-hint">
        滚轮缩放 · 拖动平移 · ← → 切换 · Esc 关闭
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.screenshot-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.92);
  display: flex;
  flex-direction: column;
  user-select: none;
}

/* Toolbar */
.sm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: rgba(30, 30, 30, 0.95);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.sm-toolbar-left,
.sm-toolbar-center,
.sm-toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sm-filename {
  font-size: 12px;
  color: #ccc;
  font-family: var(--font-mono, monospace);
}

.sm-zoom-level {
  font-size: 11px;
  color: #aaa;
  font-family: var(--font-mono, monospace);
  min-width: 45px;
  text-align: center;
}

.sm-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 26px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  background: transparent;
  color: #ccc;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.sm-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.sm-btn-close:hover {
  background: rgba(239, 68, 68, 0.3);
  color: #ef4444;
}

/* Image Container */
.sm-image-container {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  position: relative;
}

.sm-image-container.dragging {
  cursor: grabbing;
}

.sm-image {
  max-width: none;
  max-height: none;
  transition: transform 0.05s ease;
  will-change: transform;
}

/* Hint */
.sm-hint {
  text-align: center;
  padding: 6px 0;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.35);
  flex-shrink: 0;
}
</style>
