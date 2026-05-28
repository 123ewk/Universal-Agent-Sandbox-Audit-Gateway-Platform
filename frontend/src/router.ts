/**
 * Vue Router — ShadowOS 路由配置
 *
 * 页面结构：
 *   /                — 首页：任务创建
 *   /monitor/:id     — 监控面板：实时追踪 Session 执行
 *   /history         — 历史记录（P2）
 */
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/TaskCreate.vue'),
    },
    {
      path: '/monitor/:id',
      name: 'monitor',
      component: () => import('@/views/MonitorView.vue'),
      props: true,
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('@/views/HistoryView.vue'),
    },
    {
      path: '/replay/:id',
      name: 'replay',
      component: () => import('@/views/ReplayView.vue'),
      props: true,
    },
  ],
})

export default router
