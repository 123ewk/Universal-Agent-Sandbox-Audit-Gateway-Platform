"""
Sandbox 沙箱引擎 — Phase 6 核心模块

Skill → SandboxEngine → SandboxProvider → Playwright BrowserContext → Chromium

模块结构：
  models.py:         PageInfo / ActionResult 数据模型
  provider.py:       SandboxProvider 抽象接口
  local_provider.py: LocalPlaywrightProvider (本地 Chromium)
  engine.py:         SandboxEngine — navigate/click/type_text/screenshot/extract_text/get_page_info
  security.py:       双层防御 — URL 黑名单 + Playwright route 拦截 + 高危行为检测
  screenshot.py:     截图管理 — data/screenshots/{session_id}/step_{n}.png
"""
