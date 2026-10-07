"""callbacks.py — 保存、重新加载、预览"""
from __future__ import annotations

from pathlib import Path

from dash import (
    ALL,
    Input,
    Output,
    State,
    callback,
    ctx,
    no_update,
)

from task_planner.config_ui.form_builder import build_form
from task_planner.config_ui.writer import (
    write_env,
    write_user_yaml,
)
from task_planner.infrastructure.cog import hub
from task_planner.infrastructure.cog.entry import Scope
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.config_ui.callbacks")
# 路径：config_ui/callbacks.py → config_ui/ → task_planner/ → src/ → 项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_USER_YAML = _PROJECT_ROOT / "config" / "user.yaml"
_ENV_FILE = _PROJECT_ROOT / ".env"


def register() -> None:
    @callback(
        Output("cfg-form-container", "children"),
        Input("cfg-show-advanced", "value"),
        Input("cfg-reload-btn", "n_clicks"),
    )
    def _rebuild_form(advanced, n_clicks):
        # 点"重新加载"时先让 hub 从磁盘重读，再重新渲染表单
        if ctx.triggered_id == "cfg-reload-btn":
            try:
                from task_planner.infrastructure.cog import reload_hub
                n = reload_hub()
                logger.info("[ConfigUI] hub.reload 完成: %d 条", n)
            except Exception as e:
                logger.exception("[ConfigUI] hub.reload 失败: %s", e)

        include = bool(advanced and "advanced" in advanced)
        return build_form(include_advanced=include)

    @callback(
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
                except (TypeError, ValueError):
                    errors.append(f"{name}: 需要整数")
                    continue
            elif entry.type is float:
                try:
                    val = float(val) if val not in (None, "") else entry.default
                except (TypeError, ValueError):
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

        n_u = len(user_updates)
        n_e = len(env_updates)
        return (
            f"✅ 已保存（user.yaml: {n_u} 项，.env: {n_e} 项）。"
            f"请重启主应用使配置生效。",
            {"color": "#10b981", "marginTop": "16px", "fontWeight": "bold"},
        )
