<script setup lang="ts">
/**
 * HumanConsole — Agent 人机对话面板
 *
 * 职责：
 *  1. 展示 Agent 思考过程（thought stream）
 *  2. 展示 Agent 向用户提出的问题
 *  3. 提供回答/跳过按钮
 *  4. 展示对话历史
 *
 * 数据源：WSClient → EventBus → Reducers → sessionsStore (thoughtHistory)
 */
import { computed, ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import axios from '@/api/client'

const store = useSessionsStore()
const answerText = ref('')
const submitting = ref(false)

// 当前活跃 Session 的思考历史
const thoughts = computed(() => store.activeSession?.thoughtHistory ?? [])

// 当前活跃 Session 的状态
const status = computed(() => store.activeSession?.status ?? '')

// 最新的思考
const latestThought = computed(() => {
  const all = thoughts.value
  return all.length > 0 ? all[all.length - 1] : null
})

// 当前是否有 Agent 提问（通过轮询检测 pending questions）
// Phase 10D 会改为 WebSocket 驱动

async function answerQuestion(questionId: number, text: string): Promise<void> {
  submitting.value = true
  try {
    await axios.post(`/api/v1/questions/${questionId}/answer`, {
      answer_text: text,
    })
  } catch (e) {
    console.error('Answer failed:', e)
  } finally {
    submitting.value = false
    answerText.value = ''
  }
}

async function skipQuestion(questionId: number): Promise<void> {
  try {
    await axios.post(`/api/v1/questions/${questionId}/skip`)
  } catch (e) {
    console.error('Skip failed:', e)
  }
}
</script>

<template>
  <div class="human-console card">
    <div class="hc-header">
      <h3>Agent Console</h3>
      <span class="badge" :class="`badge-${status}`">{{ status }}</span>
    </div>

    <!-- Thought Stream -->
    <div class="hc-thoughts" v-if="thoughts.length > 0">
      <div
        v-for="(t, idx) in thoughts"
        :key="idx"
        class="hc-thought-item"
      >
        <div class="hc-thought-time">Step {{ t.step_number || idx + 1 }}</div>
        <div class="hc-thought-text">{{ t.thought }}</div>
        <div class="hc-thought-meta">
          <span class="hc-confidence">
            置信度: {{ Math.round(t.confidence * 100) }}%
          </span>
          <span class="hc-intent" v-if="t.intent">{{ t.intent }}</span>
        </div>
        <details class="hc-reasoning" v-if="t.reasoning_chain?.length">
          <summary>推理链路 ({{ t.reasoning_chain.length }} 步)</summary>
          <ol>
            <li v-for="(r, i) in t.reasoning_chain" :key="i">{{ r }}</li>
          </ol>
        </details>
      </div>
    </div>

    <!-- Empty State -->
    <div class="hc-empty" v-else>
      <p>等待 Agent 开始执行...</p>
      <p class="hc-hint">Agent 的思考过程将在此显示</p>
    </div>

    <!-- Answer Input -->
    <div class="hc-answer" v-if="latestThought && status === 'waiting_user'">
      <input
        v-model="answerText"
        class="input"
        placeholder="输入你的回答..."
        :disabled="submitting"
      />
      <div class="hc-answer-actions">
        <button
          class="btn btn-primary"
          :disabled="submitting || !answerText.trim()"
          @click="answerQuestion(0, answerText)"
        >
          回答
        </button>
        <button class="btn" :disabled="submitting" @click="skipQuestion(0)">跳过</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.human-console {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.hc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}

.hc-header h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.hc-thoughts {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.hc-thought-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.hc-thought-item:last-child {
  border-bottom: none;
}

.hc-thought-time {
  font-size: 10px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.hc-thought-text {
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.5;
  margin-bottom: 4px;
}

.hc-thought-meta {
  display: flex;
  gap: 8px;
  font-size: 10px;
}

.hc-confidence {
  color: var(--accent);
}

.hc-intent {
  color: var(--warning);
}

.hc-reasoning {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-secondary);
}

.hc-reasoning summary {
  cursor: pointer;
  color: var(--accent);
}

.hc-reasoning ol {
  margin: 4px 0 0 16px;
  padding: 0;
}

.hc-reasoning li {
  margin-bottom: 2px;
}

.hc-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.hc-hint {
  font-size: 11px;
  opacity: 0.6;
  margin-top: 4px;
}

.hc-answer {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.hc-answer-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>
