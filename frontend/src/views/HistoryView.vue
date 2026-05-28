<script setup lang="ts">
/**
 * HistoryView — 历史任务列表
 *
 * 列出所有已完成的 Agent 任务，支持按状态过滤，
 * 点击可进入 Replay 回放或 Monitor 实时监控。
 */
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { listTasks, type TaskStatus } from '@/api/client'

const router = useRouter()

const tasks = ref<TaskStatus[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const statusFilter = ref<string>('all')

onMounted(async () => {
  try {
    tasks.value = await listTasks(50, 0)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
})

const filteredTasks = computed(() => {
  if (statusFilter.value === 'all') return tasks.value
  return tasks.value.filter((t) => t.status === statusFilter.value)
})

function viewTask(task: TaskStatus): void {
  const id = task.session_id
  // 活跃任务 → 实时监控，已完成 → 回放
  const isActive = ['running', 'planning', 'pending'].includes(task.status)
  if (isActive) {
    router.push({ name: 'monitor', params: { id } })
  } else {
    router.push({ name: 'replay', params: { id } })
  }
}

function statusBadge(status: string): string {
  const map: Record<string, string> = {
    running: 'badge-running',
    planning: 'badge-running',
    pending: 'badge-pending',
    success: 'badge-success',
    completed: 'badge-success',
    failed: 'badge-failed',
    cancelled: 'badge-pending',
  }
  return map[status] ?? 'badge-pending'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '等待中',
    planning: '规划中',
    running: '执行中',
    success: '已完成',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] ?? status
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

const filters = [
  { label: '全部', value: 'all' },
  { label: '运行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
]
</script>

<template>
  <div class="history">
    <div class="hv-header">
      <h2>任务历史</h2>
      <div class="hv-filters">
        <button
          v-for="f in filters"
          :key="f.value"
          class="btn hv-filter-btn"
          :class="{ active: statusFilter === f.value }"
          @click="statusFilter = f.value"
        >{{ f.label }}</button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="hv-empty">加载中...</div>

    <!-- Error -->
    <div v-else-if="error" class="hv-empty hv-error">{{ error }}</div>

    <!-- Empty -->
    <div v-else-if="filteredTasks.length === 0" class="hv-empty">
      <p>暂无任务记录</p>
      <router-link to="/" class="btn btn-primary">创建第一个任务</router-link>
    </div>

    <!-- Task List -->
    <div v-else class="hv-list">
      <div
        v-for="task in filteredTasks"
        :key="task.task_id"
        class="hv-row"
        @click="viewTask(task)"
      >
        <div class="hv-row-left">
          <span class="badge" :class="statusBadge(task.status)">
            {{ statusLabel(task.status) }}
          </span>
          <div class="hv-desc">{{ task.task_description }}</div>
        </div>
        <div class="hv-row-right">
          <span class="hv-metric">
            {{ task.total_steps_executed }}/{{ task.total_steps }} steps
          </span>
          <span class="hv-metric">${{ task.llm_cost }}</span>
          <span class="hv-metric hv-date">{{ formatDate(task.created_at) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history {
  max-width: 960px;
  margin: 40px auto 0;
  padding: 0 20px;
}

.hv-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.hv-header h2 {
  font-size: 20px;
  font-weight: 600;
}

.hv-filters {
  display: flex;
  gap: 8px;
}

.hv-filter-btn {
  padding: 4px 12px;
  font-size: 12px;
}

.hv-filter-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

/* List */

.hv-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hv-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  transition: border-color .15s, background .15s;
}

.hv-row:hover {
  border-color: var(--accent);
  background: rgba(88, 166, 255, .04);
}

.hv-row-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.hv-desc {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}

.hv-row-right {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-shrink: 0;
}

.hv-metric {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.hv-date {
  min-width: 140px;
  text-align: right;
}

/* Empty */

.hv-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 60px 0;
  color: var(--text-secondary);
  font-size: 14px;
}

.hv-error {
  color: var(--danger);
}
</style>
