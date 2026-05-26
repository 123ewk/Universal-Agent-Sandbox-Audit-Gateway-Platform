"""pytest 全局配置"""
import sys
import asyncio
import pytest_asyncio  # noqa: F401 — 注册 asyncio marker


# Windows 上 asyncpg 需要 SelectorEventLoop 而非 ProactorEventLoop
# ProactorEventLoop 不支持子进程和某些 socket 操作，
# 而 asyncpg 底层依赖 selectors 模块的 I/O 多路复用。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
