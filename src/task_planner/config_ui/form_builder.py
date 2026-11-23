"""
form_builder.py — 从 cog.hub 读取配置，自动生成 Dash 表单组件
"""

from typing import Any

from dash import dcc, html

from task_planner.infrastructure.cog import hub
from task_planner.infrastructure.cog.entry import ConfigEntry, Scope
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger(__name__)


def _field_id(name: str) -> dict:
    return {"type": "cfg-field", "name": name}


def _render_input(entry: ConfigEntry, current: Any) -> Any:
    """按 entry.type 渲染对应组件"""
    if entry.type is bool:
        return dcc.Checklist(
            options=[{"label": "启用", "value": "on"}],
            value=["on"] if current else [],
            id=_field_id(entry.name),
        )
    if entry.type is int:
        return dcc.Input(
            type="number",
            value=current,
            style={"width": "200px"},
            id=_field_id(entry.name),
        )
    if entry.type is float:
        return dcc.Input(
            type="number",
            step=0.1,
            value=current,
            style={"width": "200px"},
            id=_field_id(entry.name),
        )
    # 密钥输入用密码框
    input_type = "password" if entry.secret else "text"
    return dcc.Input(
        type=input_type,
        value=current,
        style={"width": "320px"},
        id=_field_id(entry.name),
    )


def build_form(include_advanced: bool = False) -> list:
    """
    生成表单。

    Args:
        include_advanced: 是否展示 developer scope 字段（只读）
    """
    rows: list = []
    snap = hub.snapshot()

    # ── 1. 密钥字段（from_env + secret）──────────────────────
    secret_entries = [e for e in snap if e.from_env and e.secret]
    if secret_entries:
        rows.append(html.H3("🔑 密钥（保存到 .env）"))
        for e in secret_entries:
            rows.append(
                html.Div(
                    [
                        html.Label(e.purpose or e.name, style={"fontWeight": "bold"}),
                        html.Br(),
                        _render_input(e, e.value),
                        html.Br(),
                        html.Small(
                            f"环境变量名：{e.env_name}",  # ✅ 不再需要 `or e.name`
                            style={"color": "#64748b"},
                        ),
                    ],
                    style={"margin": "12px 0"},
                )
            )

    # ── 2. 用户可改的普通配置 ────────────────────────────────
    user_entries = [e for e in snap if e.scope == Scope.USER and not e.secret]
    if user_entries:
        rows.append(html.H3("🎨 用户偏好（保存到 user.yaml）"))
        for e in user_entries:
            rows.append(
                html.Div(
                    [
                        html.Label(e.purpose or e.name, style={"fontWeight": "bold"}),
                        html.Br(),
                        _render_input(e, e.value),
                    ],
                    style={"margin": "12px 0"},
                )
            )

    # ── 3. 防御性检查：orphan secrets ────────────────────
    # 理论上 entry.py __post_init__ 已拦截，这里留痕防止绕过
    orphan_secrets = [e for e in snap if e.secret and not e.from_env]
    if orphan_secrets:
        logger.error(
            "[FormBuilder] ⚠️ 发现 %d 个 orphan secret（entry.py 校验未生效！）: %s",
            len(orphan_secrets),
            [e.name for e in orphan_secrets],
        )
        rows.append(html.Hr())
        rows.append(html.H3("⚠️ 配置异常（schema 校验失效）", style={"color": "#dc2626"}))
        rows.append(
            html.P(
                "以下字段 secret=True 但 from_env=False，entry.py 校验应已拦截。"
                "请联系开发者检查 ConfigEntry 创建路径。",
                style={"color": "#dc2626", "fontSize": "13px"},
            )
        )
        for e in orphan_secrets:
            rows.append(
                html.Div(
                    [
                        html.Label(
                            e.purpose or e.name, style={"fontWeight": "bold", "color": "#dc2626"}
                        ),
                        html.Br(),
                        html.Small(
                            f"name={e.name}, scope={e.scope.value}",
                            style={"color": "#94a3b8", "fontFamily": "monospace"},
                        ),
                    ],
                    style={
                        "margin": "12px 0",
                        "padding": "8px",
                        "border": "1px solid #dc2626",
                        "borderRadius": "4px",
                    },
                )
            )

    # ── 4. 开发者字段（可选，只读展示）────────────────────────
    if include_advanced:
        dev_entries = [e for e in snap if e.scope == Scope.DEVELOPER and not e.from_env]
        if dev_entries:
            rows.append(html.Hr())
            rows.append(html.H3("🔧 开发者配置（只读）"))
            rows.append(
                html.P(
                    "如需修改，请编辑 config/schema.yaml",
                    style={"color": "#94a3b8", "fontSize": "13px"},
                )
            )
            for e in dev_entries:
                rows.append(
                    html.Div(
                        [
                            html.Span(e.name, style={"fontFamily": "monospace"}),
                            html.Span(" = ", style={"color": "#94a3b8"}),
                            html.Span(repr(e.value), style={"fontFamily": "monospace"}),
                            html.Br(),
                            html.Small(
                                e.purpose or "-",
                                style={"color": "#94a3b8", "fontSize": "12px"},
                            ),
                        ],
                        style={"margin": "8px 0"},
                    )
                )

    return rows
