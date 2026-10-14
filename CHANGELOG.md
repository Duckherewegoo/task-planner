# Changelog

本项目采用 **per-module changelog**——每个模块的历史变更集中在此。
代码里只保留「最新一段 + 指针」，完整历史在这里。

---

## `src/task_planner/core/graph/nodes/_executor.py`

  ── v5 ──
  ✅ P5-1：NodeExecResult 从 TypedDict → Pydantic BaseModel。
           收益：消费方（execute.py）用属性访问（result.status）而非
           下标访问（result["status"]）——IDE 补全 + 拼错字段立刻报错。
           _make_result 仍是唯一构造入口；frozen=True 保证不可变；
           extra="forbid" 拒绝多余字段。
  ✅ P5-2：bool 归一化逻辑抽出为 field_validator——execute_single_node
           不再直接调 _coerce_node_id，归一化推迟到构造时（单点保证）。
           _coerce_node_id 保留为归一化函数，供 validator 与测试复用。

  ── v4 ──
  ✅ P4-1：_coerce_node_id 加 bool 防御——bool 是 int 子类，
           True/False 会被 isinstance(int) 放行，业务上 node id 不
           应该是布尔值。与 sanitize.py 的 _type_matches 哲学对齐。
  ✅ P4-2：未知异常分支的 detail 改为整句截断——原写法只截断 str(e)，
           前缀 "💥 未知异常: " + 类型名 + ": " 不计入 _DETAIL_MAX_LEN，
           实际可达 230 字符，与 docstring 承诺的 200 不符。
  ✅ P4-3：_coerce_detail 改名 _coerce_text 并通用化——原本只服务
           detail，现在统一处理 name / details / category 三处 LLM 文本字段。
           顺带修掉 str(None) → "None" 污染 prompt / UI 的问题。
  ✅ P4-4：category 取值从 str(intent.get("category", "other")) 改为
           _coerce_text(...) or "other"——同一类 None 处理问题。

  ── v3 ──
  ✅ P3-1：_failed_result 重构为 _make_result，timeout 复用。
  ✅ P3-2：detail 从 LLM 层返回后做类型防御。
  ✅ P3-3：node_id 运行时校验：非 int/str 时降级为 str(...)。
  ✅ P3-4：changelog 补说明——"开始执行节点" 日志的时机。

  ── v2 ──
  ✅ P1-1：prompt 构建阶段的异常（render_template / json.dumps）
           从 try 外移入 try 内，纳入异常分级响应。
  ✅ P1-2：修正 docstring——LLMCancelledError 时向上抛。
  ✅ P1-3：新增 NodeExecResult TypedDict。
  ✅ P2-1：node 非 dict / meta 非 dict 加防御。
  ✅ P2-2：req_id 空串语义收紧。
  ✅ P2-3：detail 截断 200 字符、retryable=False 保守策略。

  ── v1 ──
  ✅ execute_single_node：构建 Prompt + 委托 LLM + 异常分级响应

---

## `src/task_planner/core/graph/nodes/sanitize.py`

  ── v4 ──
  ✅ P3-1：_FIELD_TYPE_MAP 从 SafeNodeView.__annotations__ 派生，
           消灭与真源的同步漂移；bool 防御逻辑改为对 type 集合判断，
           对元组类型同样生效。
  ✅ P3-2：模块加载时 assert 真源字段 == SafeNodeView 字段，
           配置不一致在 import 阶段就炸，而不是运行时静默降级。
  ✅ P3-3：非 dict 输入直接 return {}，去掉无意义的 cast。
  ✅ P3-4：审计日志补 input_len / pattern_hits 两个关键字段。
  ✅ P3-5：docstring 补"SENSITIVE_PATTERNS 顺序敏感、不短路"的契约。

  ── v3 ──
  ✅ P2-1：SafeNodeView 从 cast 声称改为字段级校验。
  ✅ P2-2：node 非 dict / 非 list 防御。
  ✅ P2-3：USER_VISIBLE_NODE_FIELDS 转 frozenset。
  ✅ P2-4：sanitize_input 惰性计算 hash。
  ✅ P2-5：sanitize_input 防御非 str 输入。

  ── v2 ──
  ✅ P1-1：get_cancel_event 改为从 utils.context 转发。

  ── v1 ──
  ✅ SafeNodeView + sanitize_node_for_user / sanitize_nodes_for_user
  ✅ sanitize_input（敏感信息脱敏）
  ✅ get_cancel_event（本地实现）

---

## `src/task_planner/core/graph/state.py`

  ── v8 ──
  ✅ Patch A：needs_planning 默认值改为 True。
              与 workflow.py 的 _route_start 契约对齐——新任务默认走
              intent 由意图节点判断，避免 create_initial_state 后
              直接跳 direct_answer、intent 永不执行。
  ✅ P7-1：导入分组按 PEP 8 整理（stdlib / 第三方空行分隔）。
  ✅ P7-2：三指针优先级 docstring 补"消费方"说明——当前
           execute.py / route_after_execute 均未消费该字段，
           提醒下一轮迭代必须补齐实现方。

  ── v7 ──
  ✅ P6-1：明确目标 Python 版本为 3.14。撤销 v5 的 try/except 回退。
  ✅ P6-2：移除 `from __future__ import annotations`。
  ✅ P6-3：_replace_list 改用 PEP 695 内联泛型语法。
  ✅ P6-4：模块顶部显式声明"目标运行时"。

  ── v6 ──
  ✅ P5-1：恢复 NodeResult TypedDict。
  ✅ P5-2：NodeResult.node_id 定为 int | str，与 _executor.py 契约对齐。
  ✅ P5-3：_replace_list 泛型化。
  ✅ P5-4：validate_user_action 参数放宽为 str | UserAction。
  ✅ P5-5：create_initial_state 内 node_results 显式标注。

  ── v5 ──
  ✅ P4-1：StrEnum / NotRequired 版本策略统一（当时假设支持 3.10）。
  ✅ P4-2：删除未使用的 TypedDict 导入。

  ── v4 ──
  ✅ P3-1：修正 v2 changelog 笔误——validate_user_action 并未删除。
  ✅ P3-2：_replace_list 对 None 做防御。
  ✅ P3-3：create_initial_state 显式拒绝 kwargs 覆盖必填字段。
  ✅ P3-4：NodeStatus 加 docstring。

  ── v3 ──
  ✅ P2-1：steps / node_results 用 Annotated[..., _replace_list] 显式声明覆盖语义。
  ✅ P2-2：所有非必填字段标 NotRequired。
  ✅ P2-3：集中声明三指针优先级。

  ── v2 ──
  ✅ P1-1：移除 steps / node_results 的 operator.add reducer。
  ✅ P1-2：删除未使用的 UserActionLiteral / NodeResult 类型别名。
  ✅ P1-3：清理 view_mode 的历史决策注释。

  ── v1 ──
  ✅ UserAction / NodeStatus 枚举
  ✅ TaskState 继承 MessagesState
  ✅ schema_version 字段（checkpoint 兼容）

---

## `src/task_planner/core/graph/workflow.py`

  ── v4 ──
  ✅ Patch J：resume_graph_async docstring 明确契约——
              本函数**只更新状态，不续跑图**。调用方拿到 next_nodes 后
              必须自行 `g.astream(None, config)` 让图继续执行。
              这是当前最容易被误解的点：函数名"resume"暗示"一调就恢复"，
              但 LangGraph 的 update_state 语义是"只写状态不推进"。
              不改名（会破坏现有调用方），但把隐含契约显式化。
  ✅ Patch K：顶部 checkpointer 迁移说明补一句——异步 checkpointer
              的资源释放需要 await，atexit 无法 await。迁移时必须在
              ASGI lifespan shutdown 里 await _cleanup_checkpointers_async()。
  ✅ Patch L：feedback["user_action"] 显式 str(action)——
              避免异步 checkpointer 反序列化时丢失枚举类型，
              导致 routes.py 的 isinstance assert 失败。

设计说明：
  本文件是"图的生命周期与断点续传"这一个主题的多侧面，
  属于高内聚单职责，按 utils/ 同策略保持单文件，不做拆分。

⚠️ checkpointer 现状：
   目前为同步版（Memory / Sqlite / Postgres）。
   async API 通过 asyncio.to_thread 把同步 IO 放进线程池，
   立即获得正确的异步语义，不需要先迁移 checkpointer。

   未来迁移到 AsyncSqliteSaver / AsyncPostgresSaver 时的完整清单：
     1. 把 `await asyncio.to_thread(g.get_state, config)`
        机械替换为 `await g.aget_state(config)`；
        `g.update_state` → `await g.aupdate_state`。
     2. 把 atexit 注册的 `_cleanup_checkpointers` 替换为
        在 ASGI lifespan shutdown 里 `await _cleanup_checkpointers_async()`。
        ⚠️ atexit 无法 await——异步 saver 的资源不会被同步清理释放，
           进程退出时会看到 ResourceWarning 或连接被 GC 强制断开。
     3. _build_checkpointer 换成异步工厂，在图首次构建时 await 建立连接。

⚠️ 与 execute.py 的契约变更：
   resume_graph_async 传出的 feedback 键名从 "action" / "target_node_index"
   改为 "user_action" / "retry_node_index" / "resume_from_node_index"。
   execute.py 里 `feedback.get("action")` 必须同步改为
   `feedback.get("user_action")`。

  ── v3 ──
  ✅ Patch A（state.py）：needs_planning 默认值改为 True。
  ✅ Patch B：_ATEXIT_REGISTERED 挂 sys，抗 importlib.reload。
  ✅ Patch C：_cleanup_checkpointers 失败日志从 debug 提到 warning。
  ✅ Patch D：_force_rebuild_graph 重建前先清旧资源，消除累积泄漏。
  ✅ Patch E：_LazyGraphProxy 改黑名单 + __repr__ 去硬编码。
  ✅ Patch F：resume_graph_async —— validate_user_action 校验入口；
               feedback 键名与 TaskState 字段对齐；新增
               resume_from_node_index 参数；同步图操作走 asyncio.to_thread。
  ✅ Patch G：get_thread_state_async —— 异常上抛；next 转 list；
               去掉无信息量的 has_checkpoint。
  ✅ Patch H：_run_sync 传 factory，避免协程未 await 的 RuntimeWarning。
  ✅ Patch I：build_graph docstring 加废弃提示。
  ✅ Patch F-补：图构建走 to_thread，避免首次调用阻塞事件循环。
  ✅ Patch F-补：resume_from_node_index 与 target_node_index 同时非空时
               显式报错。

  ── v2 ──
  ✅ P1-1：修复 _run_sync 报错信息中的 async 名推导 bug。
  ✅ P1-2：_graph_instance / _graph_lock 提到模块顶部。
  ✅ P1-3：_LazyGraphProxy 加白名单（后改黑名单）。
  ✅ P1-4：atexit 用 sentinel 防重复注册。
  ✅ P1-5：_cleanup_checkpointers 简化 close 分支。
  ✅ P2-1：resume_graph_async / get_thread_state_async 补齐 "next_nodes"。

  ── v1 ──
  ✅ checkpointer 资源 atexit 统一清理
  ✅ graph 单例懒加载（线程安全）
  ✅ _LazyGraphProxy 支持同步/异步 API

---

## `src/task_planner/infrastructure/logger_setup.py`

    ── v4 ──
  ✅ set_req_id 改为"有值复用"——修复同任务多 req_id 问题。

  ── v3 ──
  ✅ P2-1：set_req_id / get_req_id 增加 None 保护
  ✅ P2-2：propagate_req_id 文档补充与 asyncio.Task 的配合示例
  ✅ P2-3：TimedRotatingFileHandler 增加 delay=True，
           首次写入才创建文件，避免空日志文件占位
  ✅ P2-4：暴露 is_initialized() 供测试/健康检查使用

  ── v2 ──
  ✅ P1-6：LOG_DIR / LOG_FILE 空值兜底，回退到项目根/logs 与默认文件名
  ✅ P1-7：_SafeQueueHandler.enqueue 对 record.getMessage() 加 try，
           防止格式化失败拖垮 stderr 写入
  ✅ P1-8：shutdown_logging 清理 root_logger 上的 handler，
           避免重新初始化时重复挂 handler 导致日志翻倍
  ✅ P1-9：_project_root 模块级缓存，避免每次初始化重复计算 Path

  ── v1 ──
  ✅ P0-1：只对根 logger（"task_planner"）挂 handler，子 logger 用 propagate
  ✅ P0-2：ReqIdFilter 只在 record 无 req_id 时补默认值，不覆盖主线程注入值
  ✅ P0-3：set_req_id 的 ContextVar 语义说明；提供 propagate_req_id()
  ✅ P1-1：移除未使用的 _logger_initialized
  ✅ P1-2：保留 root logger WARNING 级别兜底，第三方库日志可见
  ✅ P1-3：shutdown_logging 用 print（listener 已停）
  ✅ P1-4：确保 task_planner.* 相关 logger 都是 ReqIdLogger
  ✅ P1-5：QueueHandler 队列满时记录 stderr 警告，不再静默丢弃

---

## `src/task_planner/infrastructure/session_store/protocol.py`

  ── v2 ──
  ✅ P1-1：消除隐式依赖。原 set() 内部读 session.start_time，
           导致 store 耦合 session 的内部结构。
           改为 store 自记 created_at，真正与 session 解耦。
  ✅ P1-2：Protocol 加 @runtime_checkable，
           允许 isinstance(obj, SessionStore) 检查（浅检方法存在性）。
  ✅ P1-3：cleanup_stale 的 TTL 判断基于 store 自己的时间，
           不再受 session 内部时钟影响。

  __ V3 __
  将源文件拆包，只保留独立的protocol接口，便于修改和观察

  ── v1 ──
  ✅ 提供 SessionStore Protocol + InMemorySessionStore 实现

---

## `src/task_planner/utils/context.py`

  ── v2 ──
  ✅ P1-1：新增 OperationCancelled 异常类。
           raise_if_cancelled() 改为抛 OperationCancelled，
           不再用 asyncio.CancelledError（该异常在 asyncio 生态中
           专用于"外部 task.cancel()"，与"用户主动取消"语义冲突，
           会被 stream/agent 层误捕为外部取消）。
  ✅ P1-2：__all__ 补 OperationCancelled。

  ── v1 ──
  ✅ get_cancel_event() / is_cancelled() / raise_if_cancelled() 便捷 API
  ✅ bind_cancel_event() 作为 cancel_scope 的语义化别名
  ✅ 文档说明 ContextVar 的跨 Task 语义（拷贝 vs 共享）

---

## `src/task_planner/utils/cytoscape_adapter.py`

  ── v2（KISS 加固，不影响功能）──
  ✅ P2-1：_get_edge_type 改名 get_edge_type（公开）。
           _get_edge_type 保留为 alias，向后兼容。
           原因：stream/control.py 等下游在跨模块引用它。
  ✅ P2-1fix: 不应该保留alias，要做就做彻底
  ✅ P2-2：_warned_empty_endpoints 加上限（_WARN_CACHE_MAX = 1000），
           超过后清空，避免脏数据导致内存泄漏。
  ✅ P2-3：dag_to_cytoscape 边循环中 _is_loop_edge 只调一次。
  ✅ P2-4：_node_state_class 分支改为 dict 查表。
  ✅ P2-5：抽 _icon_for_node，统一节点 icon 判断（原 node 循环内嵌 if-elif）。
  ✅ P2-6：类型注解精确化 Dict → Dict[str, Any]。

  ── v1 ──
  ✅ P0-1：新增 _char_width 函数，修复 _short_label 里的 NameError
  ✅ P0-2：_detect_cycles_cached 调用前先转 tuple，修复 lru_cache unhashable
  ✅ P0-3：补上 edge_type 变量赋值（data.edge_type）
  ✅ P1-1：_detect_cycles 返回类型注解修正为 Set[Tuple[str, str]]
  ✅ P1-2：删除 _node_state_class 未使用的 in_degree 参数
  ✅ P1-3：删除 _edge_class 未使用的 node_states 参数
  ✅ P1-4：_is_loop_edge 脏数据 warning 用 hasattr 去重
  ✅ P1-5：build_history_detail_markdown 的 icon_map 改用 STATUS_ICONS
  ✅ P1-6：统一用 _safe_int 处理 status
  ✅ P1-7：_status_icon 用 NODE_STYLES.get(0, {}) 兜底

---

## `src/task_planner/utils/flowchart_pro.py`

  ── v8（KISS 加固，不影响功能）──
  ✅ P1-5：加本地 _safe_int，替换所有 int(node.get(...))，
           容忍 None / 非法字符串。
  ✅ P1-6：build_clean_svg 里 node["id"] 改 node.get("id", "?")，
           避免 KeyError。
  ✅ P1-7：_extract_svg_from_html 的 lxml 分支加 except Exception，
           捕获 XMLSyntaxError 等 lxml 特有异常。
  ✅ P1-8：render_interactive 用 rsplit 精确替换**最后一个** </body>，
           避免多 </body> 场景污染结构。
  ✅ P1-9：_validate_dag 的 nx.simple_cycles 加上限 _MAX_CYCLES_DETECT=50，
           超过后只记录数量不展开。
  ✅ P2-7：_extract_svg_from_html 用 html.unescape（顶部已 import html）。
  ✅ P2-8：_render_png_pdf 的 import base64 提到模块顶部。
  ✅ P2-9：get_edge_style 的 "soft" 改用 EDGE_TYPE_SOFT 常量。
  ✅ P2-10：__init__ 注释与代码对齐（去掉"死 try/except"措辞）。
  ✅ P2-11：_build_tooltip / _build_card_data 的 node['id'] 改 .get。

  ── v7.1 ──
  ✅ P0-1：_get_details 修复字段优先级（node.details > meta.details > description）
  ✅ P0-2：build_clean_svg 兼容 edge 的 from/to 与 source/target 双契约
  ✅ P1-1：simple_cycles 只跑一次，cycle_edges 从 _validate_dag 返回
  ✅ P1-2：render_interactive 加 try/except，失败降级到 _error_html
  ✅ P1-3：删除 __init__ 里的死 try/except
  ✅ P1-4：export 的 dot 分支 except ImportError 冗余去除
  ✅ P2-x：移除 render_for_gradio；预编译 DAG_LAYOUT JSON；
           属性值 html.escape；import re 提到顶部

---

## `src/task_planner/utils/presentation_utils.py`

  ── v2（KISS 加固）──
  ✅ P2-1：_display_truncate 改累积宽度，从 O(n²) 降到 O(n)。
           原实现每次迭代重算 wcswidth(truncated + ch)，长文本下明显变慢。
  ✅ P2-2：_display_truncate 的省略号宽度从 _ELLIPSIS 常量推导，
           不再硬编码 1，未来换符号不用改代码。
  ✅ P2-3：修正注释——不声称"与 flowchart_pro 完全一致"，
           因为后者的 _short_label 会先清停用词，两者语义不同。
  ✅ P2-4：extract_first_task_id 的 else 分支显式拒绝 dict/tuple，
           避免 str({'a': 1}) 产出垃圾字符串。

  ── v1 ──
  ✅ P1-1：build_history_dropdown_options 用 wcswidth 按显示宽度截断
  ✅ P1-2：extract_first_task_id 去掉冗余的空列表判断
  ✅ P1-3：extract_first_task_id 加类型守卫

---

## `src/task_planner/utils/pyvis_export.py`

  ── v4（KISS 加固）──
  ✅ P1-5：顶部 logger import 改为直接导入，去掉 try/except 反模式。
           内部模块不可用时应当立即暴露问题，而非静默降级。
  ✅ P1-6：_shorten_label 改累积宽度，O(n²) → O(n)。
  ✅ P1-7：4 个 export 函数去掉"整体 try/except 打日志再重抛"，
           改为只在核心渲染步骤捕获，异常栈更清晰。
  ✅ P2-4：删除文件末尾废弃的 _render_pyvis_html 注释块。
  ✅ P2-5：加 __all__，明确公开接口。

  ── v3.0 ──
  ✅ P0-1：export_from_dag_store 的 dot 分支不再传递 output_path
  ✅ P0-2：边样式按 edge["type"] 区分
  ✅ P1-1：export_dag_to_png 的 dpi 参数真正生效
  ✅ P1-2：export_dag_to_svg 默认 dpi 从 800 改为 96
  ✅ P1-3：节点 label 截断改用 wcswidth 显示宽度
  ✅ P1-4：title 注入 DOT 前用 _escape_dot_text 转义
  ✅ P2-1：删除未使用的 config 导入
  ✅ P2-3：跳过无 ID 节点时打印节点内容

---

