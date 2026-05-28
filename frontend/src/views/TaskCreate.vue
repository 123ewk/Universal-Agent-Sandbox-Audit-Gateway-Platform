<script setup lang="ts">
/**
 * TaskCreate — 任务创建页面
 *
 * 用户输入自然语言任务描述 → 提交 → 跳转监控面板
 */
import { ref, inject } from 'vue'
import { useRouter } from 'vue-router'
import { createTask } from '@/api/client'
import { useSessionsStore } from '@/stores/sessions'
import type { WSClient } from '@/runtime'

const router = useRouter()
const sessionsStore = useSessionsStore()
const wsClient = inject<WSClient>('wsClient')!

const examples = [
  '打开 GitHub 首页，搜索 "langchain"，截取搜索结果',
  '打开百度，搜索 "今日天气"，截图保存',
  '访问 Hacker News，提取首页前 5 条新闻标题',
]

const taskDescription = ref('')
const maxSteps = ref(30)
const submitting = ref(false)
const error = ref<string | null>(null)

async function onSubmit(): Promise<void> {
  const desc = taskDescription.value.trim()
  if (!desc) return

  submitting.value = true
  error.value = null

  try {
    const result = await createTask({
      task_description: desc,
      max_steps: maxSteps.value,
    })

    // 初始化前端 Session 状态
    sessionsStore.initSession(result.session_id, desc)

    // 连接 WebSocket 订阅事件
    wsClient.connect(result.session_id)

    // 跳转监控面板
    router.push({ name: 'monitor', params: { id: result.session_id } })
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '请求失败'
    error.value = msg
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="task-create">
    <div class="tc-hero">
      <h1>ShadowOS</h1>
      <p>输入任务描述，Agent 将自动执行浏览器操作并实时反馈</p>
    </div>

    <form class="card tc-form" @submit.prevent="onSubmit">
      <label class="tc-label" for="task-desc">任务描述</label>
      <textarea
        id="task-desc"
        v-model="taskDescription"
        class="textarea"
        placeholder="例如：打开百度首页，搜索'今日天气'，截取搜索结果页"
        :disabled="submitting"
      />

      <div class="flex items-center gap-12 mt-16">
        <div>
          <label class="tc-label" for="max-steps">最大步数</label>
          <input
            id="max-steps"
            v-model.number="maxSteps"
            class="input"
            type="number"
            min="1"
            max="200"
            style="width: 100px"
            :disabled="submitting"
          />
        </div>
        <button
          type="submit"
          class="btn btn-primary"
          :disabled="submitting || !taskDescription.trim()"
        >
          {{ submitting ? '提交中...' : '启动 Agent' }}
        </button>
      </div>

      <p v-if="error" class="tc-error">{{ error }}</p>
    </form>

    <div class="tc-examples card">
      <h3>示例任务</h3>
      <ul>
        <li
          v-for="ex in examples"
          :key="ex"
          @click="taskDescription = ex"
        >{{ ex }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.task-create {
  max-width: 680px;
  margin: 80px auto 0;
  padding: 0 20px;
}

.tc-hero {
  text-align: center;
  margin-bottom: 32px;
}

.tc-hero h1 {
  font-size: 32px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 1px;
}

.tc-hero p {
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 14px;
}

.tc-form {
  display: flex;
  flex-direction: column;
}

.tc-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
  margin-bottom: 6px;
}

.tc-error {
  margin-top: 12px;
  color: var(--danger);
  font-size: 13px;
}

.tc-examples {
  margin-top: 24px;
}

.tc-examples h3 {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 10px;
}

.tc-examples li {
  padding: 6px 0;
  font-size: 13px;
  color: var(--accent);
  cursor: pointer;
  transition: opacity .1s;
}

.tc-examples li:hover {
  opacity: .7;
}
</style>
