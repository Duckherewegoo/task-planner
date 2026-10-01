"""callbacks — 触发所有回调注册。import 副作用。"""
from . import export_cb, graph_js, history, new_task  # noqa: F401


def register_all() -> None:
    """显式注册（供 ui/__init__.py 调用）"""
    graph_js.register()
    new_task.register()
    history.register()
    export_cb.register()
