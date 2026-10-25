"""callbacks.py — 保存、重新加载、预览。

本次重构：
  - register() → register_config_callbacks(app, with_drawer=...)
  - @callback → @app.callback（显式绑定，行为等价，注册时机自文档化）
  - 新增 _register_drawer_callbacks：URL 驱动的 Offcanvas 开关（仅主应用）
  - 保存回调升级：写盘后调 reload_hub() 热重载，区分 user.yaml / .env 提示
  - 尝试 load_dotenv(override=True)，装了 python-dotenv 则密钥也能全热更
"""

from pathlib import Path

from dash import ALL, Input, Output, State, ctx, no_update

from task_planner.config_ui.form_builder import build_form
from task_planner.config_ui.writer import write_env, write_user_yaml
from task_planner.infrastructure.cog import hub
from task_planner.infrastructure.cog.entry import Scope
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.config_ui.callbacks")

# 路径：config_ui/callbacks.py → config_ui/ → task_planner/ → src/ → 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_USER_YAML = _PROJECT_ROOT / "config" / "user.yaml"
_ENV_FILE = _PROJECT_ROOT / ".env"


# ═══════════════════════════════════════════════════════════════════
# 抽屉开关样式常量（回调每次返回完整 style 会刷屏 diff，
# 拆出基础样式，只在回调里改 display 字段）
# ═══════════════════════════════════════════════════════════════════
_OVERLAY_BASE_STYLE = {
    "position": "fixed",
    "top": 0,
    "left": 0,
    "width": "100vw",
    "height": "100vh",
    "backgroundColor": "rgba(15,23,42,0.4)",
    "zIndex": 9998,
}

_DRAWER_BASE_STYLE = {
    "position": "fixed",
    "top": 0,
    "right": 0,
    "width": "560px",
    "maxWidth": "95vw",
    "height": "100vh",
    "backgroundColor": "white",
    "boxShadow": "-4px 0 24px rgba(15,23,42,0.15)",
    "zIndex": 9999,
    "overflowY": "auto",
}


# ═══════════════════════════════════════════════════════════════════
# 对外注册入口
# ═══════════════════════════════════════════════════════════════════
def register_config_callbacks(app, *, with_drawer: bool = False) -> None:
    """把配置面板的所有回调注册到指定 Dash 应用。

    Args:
        app: dash.Dash 实例。主应用传主 app；独立 CLI 传 config_app。
        with_drawer: 是否额外注册「URL 驱动 Offcanvas 开关」的回调。
                     主应用传 True；独立 CLI 传 False。

    Notes:
        本函数必须在 app.layout 赋值之前调用——Dash 的 CallbackGraph
        在 layout 确定时构建，晚于 layout 注册的回调会触发
        「Output 引用不存在组件」异常。
    """
    if with_drawer:
        _register_drawer_callbacks(app)
    _register_form_callbacks(app)


# ═══════════════════════════════════════════════════════════════════
# URL → 抽屉开关（主应用专用）
# ═══════════════════════════════════════════════════════════════════
def _register_drawer_callbacks(app) -> None:
    """URL 是抽屉开关的唯一状态源。

    支持场景：
      - 点悬浮按钮 → dcc.Link href="/config" → 客户端路由改 pathname → 打开
      - 点关闭按钮 → dcc.Link href="/" → 客户端路由改 pathname → 关闭
      - 浏览器后退/前进 → pathname 变化 → 抽屉随之开合
      - 直接访问 /config → 首帧即打开（可分享 URL）

    主页面 children 不依赖 pathname，因此开关抽屉时主页面状态零丢失。
    """

    @app.callback(
        Output("config-overlay", "style"),
        Output("config-drawer-panel", "style"),
        Input("app-url", "pathname"),
    )
    def _toggle_drawer(pathname: str | None):
        # 边界：末尾斜杠、子路径 /config/xxx 都认作「配置页」
        is_open = bool(pathname) and pathname.rstrip("/") == "/config"
        display = "block" if is_open else "none"
        return (
            {**_OVERLAY_BASE_STYLE, "display": display},
            {**_DRAWER_BASE_STYLE, "display": display},
        )


# ═══════════════════════════════════════════════════════════════════
# 表单交互（重建 / 保存）——原 register() 逻辑迁移 + 热重载增强
# ═══════════════════════════════════════════════════════════════════
def _register_form_callbacks(app) -> None:
    """重建表单 / 保存配置。

    表单字段使用 pattern-matching ID（{"type": "cfg-field", "name": ...}），
    因此保存回调可用 State({"type": "cfg-field", "name": ALL}, ...) 动态
    采集所有字段，无需硬编码字段清单——新增配置项时本文件零改动。
    """

    # ── 重建表单（切 advanced / 点重新加载） ──
    @app.callback(
        Output("cfg-form-container", "children"),
        Input("cfg-show-advanced", "value"),
        Input("cfg-reload-btn", "n_clicks"),
    )
    def _rebuild_form(advanced, n_clicks):
        # 点「重新加载」时先让 hub 从磁盘重读，再重新渲染表单
        if ctx.triggered_id == "cfg-reload-btn":
            try:
                from task_planner.infrastructure.cog import reload_hub

                n = reload_hub()
                logger.info("[ConfigUI] hub.reload 完成: %d 条", n)
            except Exception as e:
                logger.exception("[ConfigUI] hub.reload 失败: %s", e)

        include = bool(advanced and "advanced" in advanced)
        return build_form(include_advanced=include)

    # ── 保存（写盘 + 热重载） ──
    @app.callback(
        Output("cfg-status", "children"),
        Output("cfg-status", "style"),
        Input("cfg-save-btn", "n_clicks"),
        State({"type": "cfg-field", "name": ALL}, "value"),
        State({"type": "cfg-field", "name": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _save(n_clicks, values, ids):
        if not n_clicks:
            return no_update, no_update

        user_updates: dict = {}
        env_updates: dict = {}
        errors: list[str] = []

        for id_dict, val in zip(ids, values, strict=False):
            name = id_dict["name"]
            try:
                entry = hub.dev.entry(name)
            except KeyError:
                continue

            # 类型转换（Checklist 返回 list）
            if entry.type is bool:
                val = bool(val and "on" in val)
            elif entry.type is int:
                try:
                    val = int(val) if val not in (None, "") else entry.default
                except TypeError, ValueError:
                    errors.append(f"{name}: 需要整数")
                    continue
            elif entry.type is float:
                try:
                    val = float(val) if val not in (None, "") else entry.default
                except TypeError, ValueError:
                    errors.append(f"{name}: 需要小数")
                    continue

            # 按 scope 分发
            if entry.from_env and entry.secret:
                env_updates[entry.env_name or name] = str(val)
            elif entry.scope == Scope.USER:
                user_updates[name] = val

        if errors:
            return (
                "❌ 校验失败：" + "; ".join(errors),
                {"color": "#ef4444", "marginTop": "16px", "fontWeight": "bold"},
            )

        # ── 写盘（writer.py 已实现原子写 + 保留注释） ──
        try:
            if user_updates:
                write_user_yaml(_USER_YAML, user_updates)
            if env_updates:
                write_env(_ENV_FILE, env_updates)
        except Exception as e:
            return (
                f"❌ 写入失败：{e}",
                {"color": "#ef4444", "marginTop": "16px", "fontWeight": "bold"},
            )

        # ── 热重载（关键路径） ──
        # 分三步：
        #   1) 若装了 python-dotenv，尝试把 .env 重新注入进程环境，
        #      让 from_env 字段在 reload_hub() 时读到新值
        #   2) 调 reload_hub() 让 hub 内部 store 重建
        #   3) 根据 env_updates 是否为空，给出不同提示文案
        env_hot_ok = False
        if env_updates:
            try:
                from dotenv import load_dotenv

                load_dotenv(_ENV_FILE, override=True)
                env_hot_ok = True
                logger.info("[ConfigUI] .env 已重新注入进程环境")
            except ImportError:
                logger.info("[ConfigUI] 未安装 python-dotenv，.env 需重启进程后生效")
            except Exception as e:
                logger.warning("[ConfigUI] .env 重新注入失败: %s", e)

        hot_ok = False
        try:
            from task_planner.infrastructure.cog import reload_hub

            n = reload_hub()
            hot_ok = True
            logger.info("[ConfigUI] hub.reload 成功: %d 条", n)
        except Exception as e:
            logger.exception("[ConfigUI] hub.reload 失败: %s", e)

        # ── 组合提示文案 ──
        n_u = len(user_updates)
        n_e = len(env_updates)

        if not hot_ok:
            # reload_hub 都挂了，只能建议重启
            return (
                f"✅ 已保存（user.yaml: {n_u} 项，.env: {n_e} 项）。"
                f"⚠️ 热重载失败，请重启主应用使配置生效。",
                {"color": "#d97706", "marginTop": "16px", "fontWeight": "bold"},
            )

        if env_updates and not env_hot_ok:
            # 只有 user 部分热更，env 需重启
            return (
                f"✅ 已保存（user.yaml: {n_u} 项，.env: {n_e} 项）。"
                f"用户配置已热重载生效；密钥需重启主应用后生效"
                f"（或安装 python-dotenv 实现密钥热更新）。",
                {"color": "#d97706", "marginTop": "16px", "fontWeight": "bold"},
            )

        # 完全热更成功
        return (
            f"✅ 已保存并热重载（user.yaml: {n_u} 项，.env: {n_e} 项）。",
            {"color": "#10b981", "marginTop": "16px", "fontWeight": "bold"},
        )
