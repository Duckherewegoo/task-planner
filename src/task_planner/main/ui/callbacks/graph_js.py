"""graph_js.py — 客户端回调（fit / zoom）"""
from __future__ import annotations

import json

from dash import Input, Output, clientside_callback

from task_planner.infrastructure.assets.cytoscape_js import (
    FIT_JS_TEMPLATE, GRAPH_CONFIGS, ZOOM_BTN_JS_TEMPLATE,
    ZOOM_SLIDER_JS_TEMPLATE)


def register() -> None:
    for _key, _cfg in GRAPH_CONFIGS.items():
        clientside_callback(
            FIT_JS_TEMPLATE.format(
                element_id=_cfg["element_id"],
                layout_json=json.dumps(_cfg["layout_options"]),
            ),
            Output(_cfg["fit_btn_id"], "n_clicks"),
            Input(_cfg["fit_btn_id"], "n_clicks"),
        )

        clientside_callback(
            ZOOM_BTN_JS_TEMPLATE.format(
                element_id=_cfg["element_id"],
                zoom_in_btn_id=_cfg["zoom_in_btn_id"],
                zoom_out_btn_id=_cfg["zoom_out_btn_id"],
            ),
            Output(_cfg["zoom_slider_id"], "value"),
            Input(_cfg["zoom_in_btn_id"], "n_clicks"),
            Input(_cfg["zoom_out_btn_id"], "n_clicks"),
        )

        clientside_callback(
            ZOOM_SLIDER_JS_TEMPLATE.format(element_id=_cfg["element_id"]),
            Output(_cfg["element_id"], "zoom"),
            Input(_cfg["zoom_slider_id"], "value"),
        )
