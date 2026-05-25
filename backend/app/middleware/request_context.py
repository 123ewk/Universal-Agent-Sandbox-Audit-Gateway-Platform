"""
全局请求上下文

使用 Python contextvars 实现 async-safe 的请求级变量传递。
直接使用 thread-local (threading.local) 在 asyncio 中会串数据 ——
因为多个协程共享同一条 OS 线程。而 contextvars 在每次 asyncio Task
创建时会自动深拷贝一份 Context，天然支持协程隔离。

使用方式（在任何模块）：
    from app.middleware.request_context import request_id, get_request_id
    rid = get_request_id()  # 获取当前请求的 request_id
"""
import contextvars
from uuid import uuid4

# ==================== 请求级上下文变量 ====================
# ContextVar 必须定义在模块顶层（不能放在函数内），因为每次 import 需要指向同一个对象
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="",  # 无请求时（如后台任务）返回空字符串
)

_session_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id",
    default="",
)


# ==================== 便捷读写函数 ====================
def set_request_id(rid: str) -> None:
    """设置当前协程的 request_id，仅供中间件调用"""
    _request_id_ctx.set(rid)


def get_request_id() -> str:
    """获取当前协程的 request_id，可在任何被中间件包裹的调用链中安全使用"""
    return _request_id_ctx.get()


def get_or_generate_request_id() -> str:
    """
    获取 request_id，如果未设置则生成一个新的
    用于后台任务等不在 HTTP 中间件链中的场景
    """
    rid = _request_id_ctx.get()
    if not rid:
        # 这种做法降低了唯一性保证：完整 UUID4 的冲突概率极低，但取前 16 位后，冲突概率会显著上升，不适合对唯一性要求极高的场景
        rid = uuid4().hex[:16]
        _request_id_ctx.set(rid)
    return rid


def set_session_id(sid: str) -> None:
    """设置当前协程的 session_id"""
    _session_id_ctx.set(sid)


def get_session_id() -> str:
    """获取当前协程的 session_id"""
    return _session_id_ctx.get()
