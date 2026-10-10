"""export_cb.py — 数据/图片导出回调"""
from __future__ import annotations

import asyncio
import logging

from dash import Input, Output, State, callback, dcc

from ..data_ops import make_filename, resolve_dag_data
from ..export import DATA_EXPORT_STRATEGIES, export_png_bytes, export_svg_bytes

logger = logging.getLogger(__name__)


def register() -> None:
    @callback(
        Output("export-download", "data"),
        Input("export-data-btn", "n_clicks"),
        State("history-dropdown", "value"),
        State("dag-store", "data"),
        State("data-format", "value"),
        prevent_initial_call=True,
    )
    async def on_export_data(n_clicks, history_task_ids, dag_store, fmt):
        # ✅ Bug 1 修复：node_states 从 dag_store 里取
        dag_store = dag_store or {}
        nodes, edges, _node_states, task_id_str = await resolve_dag_data(
            history_task_ids, dag_store, dag_store.get("node_states")
        )

        if not nodes:
            logger.warning("[Export] 没有节点数据可导出 (format=%s)", fmt)
            return None

        export_fn = DATA_EXPORT_STRATEGIES.get(fmt)
        if not export_fn:
            logger.error("[Export] 不支持的导出格式: %s", fmt)
            return None

        try:
            result = await asyncio.to_thread(export_fn, nodes, edges, task_id_str)
            logger.info("[Export] ✅ %s 导出成功 (task=%s)", fmt.upper(), task_id_str)
            return result
        except Exception as e:
            logger.error("[Export] %s 导出失败: %s", fmt.upper(), e, exc_info=True)
            return None

    @callback(
        Output("export-download", "data", allow_duplicate=True),
        Input("export-image-btn", "n_clicks"),
        State("history-dropdown", "value"),
        State("dag-store", "data"),
        State("node-states-store", "data"),
        State("image-format", "value"),
        prevent_initial_call=True,
    )
    async def on_export_image(n_clicks, history_task_ids, dag_store, node_states, image_format):
        nodes, edges, _node_states, task_id_str = await resolve_dag_data(
            history_task_ids, dag_store, node_states
        )

        if not nodes:
            logger.warning("[Export] 没有节点数据可导出 (format=%s)", image_format)
            return None

        title = f"流程图 {task_id_str}"

        try:
            if image_format == "svg":
                svg_bytes = await asyncio.to_thread(
                    export_svg_bytes, nodes, edges, node_states, title
                )
                logger.info(
                    "[Export] ✅ SVG 导出成功 (%.1f KB, task=%s)",
                    len(svg_bytes) / 1024, task_id_str,
                )
                return dcc.send_bytes(
                    svg_bytes,
                    filename=make_filename("flowchart", task_id_str, "svg"),
                    type="image/svg+xml",
                )
            else:
                png_bytes = await asyncio.to_thread(
                    export_png_bytes, nodes, edges, node_states, title
                )
                logger.info(
                    "[Export] ✅ PNG 导出成功 (%.1f KB, task=%s)",
                    len(png_bytes) / 1024, task_id_str,
                )
                return dcc.send_bytes(
                    png_bytes,
                    filename=make_filename("flowchart", task_id_str, "png"),
                    type="image/png",
                )
        except Exception as e:
            logger.error(
                "[Export] %s 图片渲染失败: %s",
                image_format.upper(), e, exc_info=True,
            )
            return None
