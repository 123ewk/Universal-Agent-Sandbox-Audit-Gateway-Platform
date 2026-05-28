"""
create_app() — FastAPI 应用工厂

只做四件事：
  1. middleware   — CORS + RequestID
  2. exception   — 全局异常处理器
  3. router      — 挂载所有路由
  4. lifecycle   — startup/shutdown (DB init/close)

不放业务逻辑。
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.exceptions import register_exception_handlers
from app.middleware.request_id import RequestIDMiddleware

logger = logging.getLogger(__name__)


# ====================================================================
# EventBus 订阅（启动时注册）
# ====================================================================

def _wire_event_bus() -> None:
    """
    连接 EventBus → {WebSocketManager, SessionManager}

    EventBus 分发事件时：
      - ws_handler: 构造 WS 消息 → ConnectionManager.broadcast()
      - db_handler:  追加事件到 SessionManager.event_log
    """
    from datetime import datetime, timezone
    from app.runtime.event_bus import get_event_bus
    from app.runtime.websocket_manager import get_websocket_manager
    from app.runtime.session_manager import get_session_manager

    bus = get_event_bus()
    wsm = get_websocket_manager()
    sm = get_session_manager()

    async def ws_handler(session_id: int, event: str, payload: dict) -> None:
        msg = {
            "session_id": session_id,
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        await wsm.broadcast_message(session_id, msg)

    async def db_handler(session_id: int, event: str, payload: dict) -> None:
        sm.add_event(session_id, {
            "event": event,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    bus.subscribe("ws", ws_handler)
    bus.subscribe("db", db_handler)
    logger.info("EventBus 已连接: ws + db")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
    )

    # ================================================================
    # 1. Middleware（后添加的 → 外层，先执行）
    # ================================================================

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    # ================================================================
    # 2. Exception Handlers
    # ================================================================

    register_exception_handlers(app)

    # ================================================================
    # 3. Routers
    # ================================================================

    from app.agent.router import router as agent_router
    from app.audit.router import router as audit_router

    app.include_router(agent_router, prefix="/api/v1")
    # audit_router 自带 /api/v1/approvals 前缀
    app.include_router(audit_router)

    # Screenshot router
    from app.routers.screenshots import router as screenshot_router
    app.include_router(screenshot_router)

    # ================================================================
    # 4. Lifecycle
    # ================================================================

    @app.on_event("startup")
    async def startup():
        logger.info("ShadowOS 启动中...")
        try:
            from app.database import init_db
            await init_db()
        except Exception as exc:
            logger.warning("DB 初始化跳过: %s", exc)
        _wire_event_bus()
        logger.info("ShadowOS 启动完成")

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("ShadowOS 关闭中...")
        try:
            from app.database import close_db
            await close_db()
        except Exception as exc:
            logger.warning("DB 关闭异常: %s", exc)
        logger.info("ShadowOS 已关闭")

    return app
