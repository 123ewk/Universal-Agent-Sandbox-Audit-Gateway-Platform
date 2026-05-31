<script setup lang="ts">
/**
 * MonitorView V2 — Agent Runtime OS Dashboard
 *
 * 布局:
 *   ┌──────────────────────────────────────────┐
 *   │  Status Bar: Session ID / Status / Cost  │
 *   ├────────────────┬─────────────────────────┤
 *   │  Left Panel    │  Live Browser/Screenshot│
 *   │  [Tabs]        │                          │
 *   │  Console       │                          │
 *   │  Timeline      │                          │
 *   │  Step Tree     │                          │
 *   │  Permissions   │                          │
 *   ├────────────────┴─────────────────────────┤
 *   │  Artifacts Bar                           │
 *   └──────────────────────────────────────────┘
 */
import { onMounted, inject, computed, ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import type { WSClient } from '@/runtime'
import StatusBadge from '@/components/StatusBadge.vue'
import HumanConsole from '@/components/HumanConsole.vue'
import ThoughtTimeline from '@/components/ThoughtTimeline.vue'
import StepTree from '@/components/StepTree.vue'
import ToolPermissionPanel from '@/components/ToolPermissionPanel.vue'
import BrowserPanel from '@/components/BrowserPanel.vue'
import ArtifactPanel from '@/components/ArtifactPanel.vue'
import ApprovalDialog from '@/components/ApprovalDialog.vue'
import ResultPanel from '@/components/ResultPanel.vue'

const props = defineProps<{ id: string }>()
const sessionId = computed(() => Number(props.id))

const sessionsStore = useSessionsStore()
const wsClient = inject<WSClient>('wsClient')!

const session = computed(() => sessionsStore.sessions.get(sessionId.value) ?? null)
const activeTab = ref<'console' | 'timeline' | 'steps' | 'permissions'>('console')

onMounted(() => {
  if (!sessionsStore.sessions.has(sessionId.value)) {
    sessionsStore.initSession(sessionId.value)
  }
  sessionsStore.setActiveSession(sessionId.value)
  if (!wsClient.connected || wsClient.sessionIdValue !== sessionId.value) {
    wsClient.connect(sessionId.value)
  }
})
</script>

<template>
  <div class="monitor-v2">
    <!-- ============================================================ -->
    <!-- Status Bar                                                   -->
    <!-- ============================================================ -->
    <div class="mv-statusbar">
      <div class="mvs-left">
        <span class="mvs-session">Session #{{ sessionId }}</span>
        <StatusBadge v-if="session" :status="session.status" />
      </div>
      <div class="mvs-right">
        <!-- Real-time Metrics -->
        <span class="mvs-metric" v-if="session?.metrics?.model_name" :title="'Model'">
          {{ session.metrics.model_name }}
        </span>
        <span class="mvs-metric" v-if="session?.metrics">
          Prompt {{ session.metrics.prompt_tokens }} / Comp {{ session.metrics.completion_tokens }}
        </span>
        <span class="mvs-metric" v-if="session?.metrics">
          ${{ session.metrics.estimated_cost }}
        </span>
        <span class="mvs-metric" v-if="session?.metrics?.latency_ms">
          {{ session.metrics.latency_ms }}ms
        </span>
        <span class="mvs-metric" v-else-if="session && session.llmCost !== '0'">
          ${{ session.llmCost }}
        </span>
        <span class="mvs-metric mvs-step">
          Step {{ session?.totalStepsExecuted ?? 0 }}/{{ session?.planSteps.length || '?' }}
        </span>
        <span class="mvs-metric">{{ session?.progressPct ?? 0 }}%</span>
        <span class="mvs-metric mvs-url" v-if="session?.currentUrl" :title="session.currentUrl">
          {{ session.currentUrl }}
        </span>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Progress Bar                                                 -->
    <!-- ============================================================ -->
    <div class="mv-progress">
      <div
        class="mv-progress-fill"
        :class="{ 'mv-error': session?.status === 'failed' }"
        :style="{ width: (session?.progressPct ?? 0) + '%' }"
      />
    </div>

    <!-- ============================================================ -->
    <!-- Main Content                                                 -->
    <!-- ============================================================ -->
    <div class="mv-main" v-if="session">
      <!-- Left Panel (Tabs) -->
      <div class="mv-left">
        <div class="mv-tabs">
          <button class="mv-tab" :class="{ active: activeTab === 'console' }" @click="activeTab = 'console'">Console</button>
          <button class="mv-tab" :class="{ active: activeTab === 'timeline' }" @click="activeTab = 'timeline'">Timeline</button>
          <button class="mv-tab" :class="{ active: activeTab === 'steps' }" @click="activeTab = 'steps'">Steps</button>
          <button class="mv-tab" :class="{ active: activeTab === 'permissions' }" @click="activeTab = 'permissions'">Permissions</button>
        </div>
        <div class="mv-tab-content">
          <HumanConsole v-if="activeTab === 'console'" />
          <ThoughtTimeline v-else-if="activeTab === 'timeline'" />
          <StepTree v-else-if="activeTab === 'steps'" />
          <ToolPermissionPanel v-else-if="activeTab === 'permissions'" />
        </div>
      </div>

      <!-- Right Panel (Browser + Error + Cost) -->
      <div class="mv-right">
        <BrowserPanel :session-id="sessionId" />

        <!-- Result Panel -->
        <ResultPanel v-if="session.taskResult" :result="session.taskResult" />

        <!-- Error -->
        <div v-if="session.errorMessage" class="card mv-error-card">
          <h4>错误信息</h4>
          <pre>{{ session.errorMessage }}</pre>
        </div>

        <!-- Metrics Panel (always visible) -->
        <div class="card mv-metrics-card">
          <h4>Runtime Metrics</h4>
          <div class="mv-cost-row" v-if="session.metrics?.model_name">
            <span>模型</span><span class="mv-mono">{{ session.metrics.model_name }}</span>
          </div>
          <div class="mv-cost-row" v-if="session.metrics">
            <span>Prompt Tokens</span><span class="mv-mono">{{ session.metrics.prompt_tokens.toLocaleString() }}</span>
          </div>
          <div class="mv-cost-row" v-if="session.metrics">
            <span>Completion Tokens</span><span class="mv-mono">{{ session.metrics.completion_tokens.toLocaleString() }}</span>
          </div>
          <div class="mv-cost-row">
            <span>Total Tokens</span>
            <span class="mv-mono">{{ (session.metrics?.total_tokens || session.tokensUsed).toLocaleString() }}</span>
          </div>
          <div class="mv-cost-row" v-if="session.metrics?.latency_ms">
            <span>Latency</span><span class="mv-mono">{{ session.metrics.latency_ms }}ms</span>
          </div>
          <div class="mv-cost-row">
            <span>Cost</span><span class="mv-mono">${{ session.metrics?.cumulative_cost || session.llmCost }}</span>
          </div>
          <div class="mv-cost-row">
            <span>Steps</span><span class="mv-mono">{{ session.totalStepsExecuted }}/{{ session.planSteps.length || '?' }}</span>
          </div>
        </div>
      </div>

      <!-- ApprovalDialog (teleported overlay) -->
      <ApprovalDialog />
    </div>

    <!-- Loading -->
    <div class="mv-empty" v-else>
      加载 Session 状态...
    </div>

    <!-- ============================================================ -->
    <!-- Artifacts Bar (bottom)                                       -->
    <!-- ============================================================ -->
    <div class="mv-artifacts-bar" v-if="session">
      <ArtifactPanel />
    </div>
  </div>
</template>

<style scoped>
.monitor-v2 {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 45px);
}

/* Status Bar */
.mv-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 20px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  min-height: 40px;
}
.mvs-left { display: flex; align-items: center; gap: 10px; }
.mvs-session { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.mvs-cost { font-size: 12px; font-family: var(--font-mono); color: var(--success); }
.mvs-right { display: flex; align-items: center; gap: 14px; }
.mvs-metric { font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono); max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mvs-metric.mvs-step { max-width: 100px; }
.mvs-metric.mvs-url { max-width: 180px; }
.mv-mono { font-family: var(--font-mono); }

/* Progress */
.mv-progress { height: 2px; background: var(--border); }
.mv-progress-fill { height: 100%; background: var(--accent); transition: width .3s; }
.mv-progress-fill.mv-error { background: var(--danger); }

/* Main */
.mv-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Left Panel */
.mv-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-right: 1px solid var(--border);
}
.mv-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 12px;
  background: var(--bg-secondary);
}
.mv-tab {
  padding: 8px 14px;
  font-size: 11px;
  font-weight: 500;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color .15s, border-color .15s;
}
.mv-tab:hover { color: var(--text-primary); }
.mv-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.mv-tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

/* Right Panel */
.mv-right {
  width: 380px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  overflow-y: auto;
}
.mv-error-card { }
.mv-error-card h4 { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px; }
.mv-error-card pre { font-size: 11px; color: var(--danger); white-space: pre-wrap; font-family: var(--font-mono); }
.mv-metrics-card { }
.mv-metrics-card h4 { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px; }
.mv-cost-card h4 { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px; }
.mv-cost-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 11px; font-family: var(--font-mono); }
.mv-cost-row span:first-child { color: var(--text-secondary); }

/* Empty */
.mv-empty { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); }

/* Artifacts Bar */
.mv-artifacts-bar {
  border-top: 1px solid var(--border);
  padding: 10px 20px;
  background: var(--bg-secondary);
  max-height: 140px;
  overflow-y: auto;
}
</style>
