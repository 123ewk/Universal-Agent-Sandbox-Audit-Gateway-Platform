<script setup lang="ts">
/**
 * ToolPermissionPanel — 工具权限管理面板
 *
 * 展示所有已注册 Tool 的权限状态，
 * 用户可调整每个 Tool 的权限策略。
 * 策略存储在 localStorage，前端 Reducer 读取。
 */
import { ref, onMounted } from 'vue'

interface ToolPermission {
  name: string
  riskLabel: string
  riskColor: string
  policy: 'always_allow' | 'ask' | 'readonly' | 'disabled'
}

const tools = ref<ToolPermission[]>([])
const policies = ['always_allow', 'ask', 'readonly', 'disabled'] as const
const policyLabels: Record<string, string> = {
  always_allow: '始终允许',
  ask: '每次询问',
  readonly: '只读',
  disabled: '禁用',
}

onMounted(() => {
  const stored = localStorage.getItem('shadowos_tool_permissions')
  const saved: Record<string, string> = stored ? JSON.parse(stored) : {}

  // Static list for now — Phase 10D is UI framework
  const defaults: ToolPermission[] = [
    { name: 'browser_goto', riskLabel: 'L1 只读', riskColor: 'green', policy: 'always_allow' },
    { name: 'browser_screenshot', riskLabel: 'L1 只读', riskColor: 'green', policy: 'always_allow' },
    { name: 'browser_extract_text', riskLabel: 'L1 只读', riskColor: 'green', policy: 'always_allow' },
    { name: 'browser_click', riskLabel: 'L2 交互', riskColor: 'blue', policy: 'ask' },
    { name: 'browser_type', riskLabel: 'L2 交互', riskColor: 'blue', policy: 'ask' },
    { name: 'file_read', riskLabel: 'L3 文件', riskColor: 'orange', policy: 'readonly' },
    { name: 'file_write', riskLabel: 'L3 文件', riskColor: 'orange', policy: 'ask' },
    { name: 'shell_run', riskLabel: 'L4 Shell', riskColor: 'red', policy: 'ask' },
  ]

  tools.value = defaults.map((t) => ({
    ...t,
    policy: (saved[t.name] as ToolPermission['policy']) || t.policy,
  }))
})

function setPolicy(name: string, policy: string): void {
  const tool = tools.value.find((t) => t.name === name)
  if (tool) tool.policy = policy as ToolPermission['policy']
  const stored: Record<string, string> = {}
  tools.value.forEach((t) => { stored[t.name] = t.policy })
  localStorage.setItem('shadowos_tool_permissions', JSON.stringify(stored))
}
</script>

<template>
  <div class="tool-permissions">
    <h3 class="tp-title">工具权限</h3>
    <div class="tp-item" v-for="tool in tools" :key="tool.name">
      <div class="tp-info">
        <span class="tp-name">{{ tool.name }}</span>
        <span class="tp-risk" :style="{ color: `var(--${tool.riskColor === 'green' ? 'success' : tool.riskColor === 'blue' ? 'accent' : tool.riskColor === 'orange' ? 'warning' : 'danger'})` }">
          {{ tool.riskLabel }}
        </span>
      </div>
      <select class="tp-select" :value="tool.policy" @change="(e: Event) => setPolicy(tool.name, (e.target as HTMLSelectElement).value)">
        <option v-for="p in policies" :key="p" :value="p">{{ policyLabels[p] }}</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.tool-permissions { }
.tp-title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin: 0 0 8px; }
.tp-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); }
.tp-item:last-child { border-bottom: none; }
.tp-info { display: flex; flex-direction: column; gap: 2px; }
.tp-name { font-size: 11px; color: var(--text-primary); font-family: monospace; }
.tp-risk { font-size: 10px; }
.tp-select { font-size: 10px; padding: 2px 4px; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--border); border-radius: 4px; }
</style>
