"""layout.py — 配置工具页面布局"""
from dash import dcc, html

from task_planner.config_ui.form_builder import build_form


def build_layout() -> html.Div:
    return html.Div([
        html.H1("⚙️ Task Planner 配置工具"),
        html.P(
            "修改用户级配置与密钥。保存后需重启主应用生效。",
            style={"color": "#64748b"},
        ),

        html.Div([
            dcc.Checklist(
                id="cfg-show-advanced",
                options=[{"label": " 显示开发者配置（只读）", "value": "advanced"}],
                value=[],
                style={"display": "inline-block", "marginRight": "16px"},
            ),
            html.Button("💾 保存", id="cfg-save-btn", n_clicks=0,
                        style={
                            "backgroundColor": "#1a73e8", "color": "white",
                            "border": "none", "padding": "8px 16px",
                            "borderRadius": "6px", "cursor": "pointer",
                            "fontWeight": "bold", "marginRight": "8px",
                        }),
            html.Button("🔄 重新加载", id="cfg-reload-btn", n_clicks=0,
                        style={
                            "backgroundColor": "#e2e8f0", "color": "#475569",
                            "border": "none", "padding": "8px 16px",
                            "borderRadius": "6px", "cursor": "pointer",
                        }),
        ], style={"marginBottom": "16px"}),

        html.Div(
            id="cfg-form-container",
            children=build_form(include_advanced=False),
        ),

        html.Div(
            id="cfg-status",
            style={"marginTop": "16px", "fontWeight": "bold"},
        ),

        dcc.Store(id="cfg-dirty-store", data=False),
    ], style={
        "maxWidth": "900px", "margin": "40px auto",
        "padding": "24px", "fontFamily": "system-ui, sans-serif",
        "backgroundColor": "white",
        "borderRadius": "12px",
        "boxShadow": "0 4px 16px rgba(0,0,0,0.06)",
    })
