<script setup lang="ts">
/**
 * StatusBadge — 状态徽章组件
 *
 * 根据 session status 渲染不同颜色的徽章。
 */
defineProps<{ status: string }>()
</script>

<template>
  <span class="status-badge" :class="`sb-${status}`">{{ status }}</span>
</template>

<style scoped>
.status-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
  letter-spacing: .5px;
}
.sb-idle { background: var(--border); color: var(--text-secondary); }
.sb-analyzing { background: rgba(59, 130, 246, .15); color: var(--accent); }
.sb-planning { background: rgba(59, 130, 246, .15); color: var(--accent); }
.sb-running { background: rgba(34, 197, 94, .15); color: var(--success); }
.sb-executing { background: rgba(34, 197, 94, .15); color: var(--success); }
.sb-waiting_user { background: rgba(251, 146, 60, .15); color: var(--warning); }
.sb-waiting_approval { background: rgba(234, 179, 8, .15); color: var(--warning); }
.sb-completed { background: rgba(34, 197, 94, .2); color: var(--success); }
.sb-failed { background: rgba(239, 68, 68, .15); color: var(--danger); }
.sb-cancelled { background: var(--border); color: var(--text-secondary); }
</style>
