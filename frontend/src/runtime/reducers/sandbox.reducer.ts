/**
 * Sandbox Reducer — 处理 sandbox.* 事件
 *
 * 监听：sandbox.navigation / sandbox.screenshot / sandbox.page.info
 * 写入：sessions store（URL 跟踪 + 截图列表）
 */
import type { WSMessage, NavigationPayload, ScreenshotPayload, PageInfoPayload } from '../event-types'

export interface SandboxStoreLike {
  setCurrentUrl(sessionId: number, url: string, title: string): void
  addScreenshot(sessionId: number, payload: ScreenshotPayload): void
  setPageInfo(sessionId: number, payload: PageInfoPayload): void
}

export function createSandboxReducer(store: SandboxStoreLike) {
  return (msg: WSMessage): void => {
    const { event, session_id: sessionId, payload } = msg

    switch (event) {
      case 'sandbox.navigation': {
        const p = payload as unknown as NavigationPayload
        store.setCurrentUrl(sessionId, p.to_url, p.title)
        break
      }

      case 'sandbox.screenshot': {
        const p = payload as unknown as ScreenshotPayload
        store.addScreenshot(sessionId, p)
        break
      }

      case 'sandbox.page.info': {
        const p = payload as unknown as PageInfoPayload
        store.setPageInfo(sessionId, p)
        break
      }
    }
  }
}
