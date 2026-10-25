"""layout.py — 配置工具页面布局。

本次重构：
  - 拆成两个函数：
    * build_config_panel()：返回面板内容（不含卡片外壳），供主应用
      嵌入 Offcanvas、或独立 CLI 复用
    * build_layout()：返回完整布局（带卡片外壳），独立 CLI 专用
  - 组件 ID 保持原样：cfg-show-advanced / cfg-save-btn / cfg-reload-btn /
    cfg-form-container / cfg-status / cfg-dirty-store
"""

from dash import dcc, html

from task_planner.config_ui.form_builder import build_form


def build_config_panel() -> html.Div:
    """构建配置面板内容（不含卡片外壳）。

    使用方：
      - 主应用：作为 Offcanvas 侧滑面板的 children
      - 独立 CLI：作为 build_layout() 的子节点

    Returns:
        配置面板根节点（html.Div）。
    """
    return html.Div(
        [
            html.P(
                "修改用户级配置与密钥。保存后用户配置将热重载生效；"
                "密钥部分若安装了 python-dotenv 也可热更，否则需重启进程。",
                style={"color": "#64748b", "fontSize": "13px"},
            ),
            html.Div(
                [
                    dcc.Checklist(
                        id="cfg-show-advanced",
                        options=[{"label": " 显示开发者配置（只读）", "value": "advanced"}],
                        value=[],
                        style={"display": "inline-block", "marginRight": "16px"},
                    ),
                    html.Button(
                        "💾 保存",
                        id="cfg-save-btn",
                        n_clicks=0,
                        style={
                            "backgroundColor": "#1a73e8",
                            "color": "white",
                            "border": "none",
                            "padding": "8px 16px",
                            "borderRadius": "6px",
                            "cursor": "pointer",
                            "fontWeight": "bold",
                            "marginRight": "8px",
                        },
                    ),
                    html.Button(
                        "🔄 重新加载",
                        id="cfg-reload-btn",
                        n_clicks=0,
                        style={
                            "backgroundColor": "#e2e8f0",
                            "color": "#475569",
                            "border": "none",
                            "padding": "8px 16px",
                            "borderRadius": "6px",
                            "cursor": "pointer",
                        },
                    ),
                ],
                style={"marginBottom": "16px"},
            ),
            html.Div(
                id="cfg-form-container",
                children=build_form(include_advanced=False),
            ),
            html.Div(
                id="cfg-status",
                style={"marginTop": "16px", "fontWeight": "bold", "fontSize": "13px"},
            ),
            dcc.Store(id="cfg-dirty-store", data=False),
        ]
    )


def build_layout() -> html.Div:
    """独立 CLI 入口的完整布局（带卡片外壳）。

    与主应用内嵌模式的差异：
      - 主应用内嵌：build_config_panel() 直接作为 Offcanvas children，
        面板本身无卡片外壳（Offcanvas 已有白底和阴影）
      - 独立 CLI：需要卡片外壳自身包裹，否则白底铺满全屏显得空旷
    """
    return html.Div(
        [
            html.H1("⚙️ Task Planner 配置工具"),
            build_config_panel(),
        ],
        style={
            "maxWidth": "900px",
            "margin": "40px auto",
            "padding": "24px",
            "fontFamily": "system-ui, sans-serif",
            "backgroundColor": "white",
            "borderRadius": "12px",
            "boxShadow": "0 4px 16px rgba(0,0,0,0.06)",
        },
    )
