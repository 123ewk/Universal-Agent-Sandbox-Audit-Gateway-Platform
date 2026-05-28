"""技能系统：为 Agent 提供可审计、可控制的能力模块"""


def init_skills() -> int:
    """
    初始化所有技能：导入模块 → 自动发现 → 注册

    必须显式 import 每个 skill 模块，否则 __subclasses__() 无法找到它们。
    此函数在 FastAPI 启动事件中调用。
    """
    import app.skills.browser  # noqa: F401 — 触发子类注册
    import app.skills.file     # noqa: F401
    import app.skills.shell    # noqa: F401

    from app.skills.registry import registry
    return registry.discover()

