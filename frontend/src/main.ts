/**
 * ShadowOS Frontend — 入口文件
 *
 * 初始化顺序：
 *   1. Vue + Pinia + Router
 *   2. Runtime Layer（EventBus + Reducers + WSClient）
 *   3. 挂载 App
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { WSClient, EventBus, createSessionReducer, createStepReducer, createApprovalReducer, createSandboxReducer } from './runtime'
import { useSessionsStore } from './stores/sessions'
import { useApprovalsStore } from './stores/approvals'
import './styles/main.css'

// ================================================================
// 1. Create App + Plugins
// ================================================================

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

// ================================================================
// 2. Initialize Runtime Layer
// ================================================================

// 必须在 app.use(pinia) 之后获取 store 实例
const sessionsStore = useSessionsStore()
const approvalsStore = useApprovalsStore()

const eventBus = new EventBus()
eventBus.registerReducer('agent', (msg) => {
  // 按事件类型分发到 session 或 step reducer
  if (msg.event.startsWith('agent.step.') || msg.event === 'agent.thought' || msg.event === 'agent.metrics') {
    createStepReducer(sessionsStore)(msg)
  } else {
    createSessionReducer(sessionsStore)(msg)
  }
})
eventBus.registerReducer('sandbox', createSandboxReducer(sessionsStore))
eventBus.registerReducer('approval', createApprovalReducer(approvalsStore))

const wsClient = new WSClient()
wsClient.onMessage((msg) => eventBus.dispatch(msg))
wsClient.onStatusChange((connected) => {
  console.log('[WS]', connected ? 'connected' : 'disconnected')
})

// ================================================================
// 3. Provide runtime to components
// ================================================================

app.provide('wsClient', wsClient)
app.provide('eventBus', eventBus)

// ================================================================
// 4. Mount
// ================================================================

app.mount('#app')
