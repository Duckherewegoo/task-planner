"""cytoscape_js.py — 前端 JS 模板（原 config.py 第 14 节后半）"""
from typing import Any

# >>>>> GRAPH_CONFIGS  <<<<<
GRAPH_CONFIGS: dict[str, dict[str, Any]]  = {'main': {'element_id': 'flowchart', 'fit_btn_id': 'fit-btn', 'zoom_in_btn_id': 'zoom-in-btn', 'zoom_out_btn_id': 'zoom-out-btn', 'zoom_slider_id': 'zoom-slider', 'layout_options': {'name': 'dagre', 'rankDir': 'TB', 'nodeSep': 300, 'rankSep': 450, 'edgeSep': 80, 'padding': 80}}, 'history': {'element_id': 'history-flowchart', 'fit_btn_id': 'history-fit-btn', 'zoom_in_btn_id': 'history-zoom-in-btn', 'zoom_out_btn_id': 'history-zoom-out-btn', 'zoom_slider_id': 'history-zoom-slider', 'layout_options': {'name': 'dagre', 'rankDir': 'TB', 'nodeSep': 250, 'rankSep': 380, 'edgeSep': 60, 'padding': 60}}}


# >>>>> FIT_JS_TEMPLATE  <<<<<
FIT_JS_TEMPLATE = "\nfunction(n_clicks) {{\n    if (!n_clicks) return window.dash_clientside.no_update;\n    var el = document.getElementById('{element_id}');\n    // ⚠️ _cyRef 是 dash-cytoscape 非公开 API，版本升级时需验证\n    if (!el || !el._cyRef) return window.dash_clientside.no_update;\n    var cy = el._cyRef;\n    var layoutOpts = {layout_json};\n    \n    // 使用 layoutstop 事件替代硬编码 setTimeout，确保布局完成后才 fit\n    cy.once('layoutstop', function() {{\n        cy.fit(cy.elements(), 80);\n    }});\n    cy.layout(layoutOpts).run();\n    return window.dash_clientside.no_update;\n}}\n"


# >>>>> ZOOM_BTN_JS_TEMPLATE  <<<<<
ZOOM_BTN_JS_TEMPLATE = "\nfunction(n_clicks_in, n_clicks_out) {{\n    var ctx = window.dash_clientside.callback_context;\n    if (!ctx || !ctx.triggered || ctx.triggered.length === 0) {{\n        return window.dash_clientside.no_update;\n    }}\n    var el = document.getElementById('{element_id}');\n    if (!el || !el._cyRef) return 0.6;\n    var cy = el._cyRef;\n    \n    var cur = cy.zoom() || 0.6;\n    var prop_id = ctx.triggered[0].prop_id || '';\n    var ZOOM_STEP = 0.2;\n    var ZOOM_MIN = 0.2;\n    var ZOOM_MAX = 4.0;\n    \n    if (prop_id.indexOf('{zoom_in_btn_id}') !== -1) {{\n        cy.zoom(Math.min(ZOOM_MAX, cur + ZOOM_STEP));\n    }} else if (prop_id.indexOf('{zoom_out_btn_id}') !== -1) {{\n        cy.zoom(Math.max(ZOOM_MIN, cur - ZOOM_STEP));\n    }}\n    return cy.zoom() || 0.6;\n}}\n"


# >>>>> ZOOM_SLIDER_JS_TEMPLATE  <<<<<
ZOOM_SLIDER_JS_TEMPLATE = "\nfunction(val) {{\n    if (val == null) return window.dash_clientside.no_update;\n    var el = document.getElementById('{element_id}');\n    if (!el || !el._cyRef) return window.dash_clientside.no_update;\n    el._cyRef.zoom(val);\n    return window.dash_clientside.no_update;\n}}\n"
