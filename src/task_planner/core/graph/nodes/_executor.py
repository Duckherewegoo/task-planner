"""
_executor.py — 单节点执行器（nodes 包内部使用）。
对外只暴露 execute_single_node()。

── v5 ──
  ✅ P5-1：NodeExecResult 从 TypedDict → Pydantic BaseModel。
           收益：消费方（execute.py）用属性访问（result.status）而非
           下标访问（result["status"]）——IDE 补全 + 拼错字段立刻报错。
           _make_result 仍是唯一构造入口；frozen=True 保证不可变；
           extra="forbid" 拒绝多余字段。
  ✅ P5-2：bool 归一化逻辑抽出为 field_validator——execute_single_node
           不再直接调 _coerce_node_id，归一化推迟到构造时（单点保证）。
           _coerce_node_id 保留为归一化函数，供 validator 与测试复用。

⚠️ 完整变更历史见 CHANGELOG.md。
"""
import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from task_planner.infrastructure.cog import hub as _hub
from task_planner.infrastructure.llm import LLMCancelledError, LLMTimeoutError, execute_node_llm
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.prompts.loader import EXECUTE_NODE_PROMPT, render_template

logger = get_logger(__name__)

# detail 字段的长度上限（异常分支截断用，避免超长异常信息污染前端）
_DETAIL_MAX_LEN = 200

# 返回节点 ID 的类型约束。node["id"] 可能来自 LLM 输出，需要运行时防御。
_NodeId = int | str


def _coerce_node_id(raw: Any) -> _NodeId:
    """
    node["id"] 可能来自 LLM 输出，类型不可信。
    非 int/str 一律转 str，保证 NodeExecResult.node_id 的契约成立。

    ⚠️ bool 是 int 的子类——True/False 会被 isinstance(int) 放行，
       但业务上 node id 不应该是布尔值。与 sanitize.py 的
       _type_matches 处理哲学一致：凡"外部输入 + int 字段"必做 bool 特判。

    调用方：
      - NodeExecResult._normalize_node_id validator——构造时归一化
      - tests/test_package/test_executor_coerce.py——单元测试直接验证
    """
    if isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, (int, str)):
        return raw
    return str(raw)


def _coerce_text(raw: Any) -> str:
    """
    把任意值安全转成 str。用于所有"从 LLM 输入取文本字段"的场景：
      - detail / name / category 等
      - None → ""
      - 其他类型 → str(...)

    避免 str(None) 产生 "None" 字符串污染 prompt / UI。

    命名说明：通用名 _coerce_text 而不是 _coerce_detail——
    它服务的字段不止 detail，还包括 name / category。
    """
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    return str(raw)


class NodeExecResult(BaseModel):
    """
    execute_single_node 的返回形状。

    ⚠️ 与 IntentModel 的差异：
       - IntentModel 是**外部边界**（LLM 输出）——校验是防线
       - NodeExecResult 是**内部构造**（_make_result 唯一入口）——校验是
         "消费端体验"（属性访问、IDE 补全、类型明确）

    ⚠️ status 只有三种——LLMCancelledError 时向上抛，不在这里返回。
       这与 execute.py 调用方的契约一致（那里 catch LLMCancelledError
       并转为 cancel_requested=True 的状态更新）。

    不可变性：frozen=True——构造后字段只读。如需修改，用 model_copy(update=...)。
    """

    model_config = ConfigDict(
        frozen=True,     # 不可变——内部构造的"数据类"
        extra="forbid",  # 多字段报错——提醒改了字段结构要同步消费方
    )

    status: Literal["success", "failed", "timeout"]
    detail: str
    retryable: bool
    node_id: _NodeId
    node_name: str

    @field_validator("node_id", mode="before")
    @classmethod
    def _normalize_node_id(cls, v: Any) -> _NodeId:
        """
        构造时归一化 node_id——bool 特判 + 非 int/str 转 str。
        逻辑委托给 _coerce_node_id，避免重复。
        """
        return _coerce_node_id(v)


def _make_result(
    nid: _NodeId,
    name: str,
    *,
    status: Literal["success", "failed", "timeout"],
    detail: str,
    retryable: bool,
) -> NodeExecResult:
    """
    构造 NodeExecResult 的唯一入口。所有返回路径都走这里，
    保证形状与类型在任何分支下都一致。
    """
    return NodeExecResult(
        status=status,
        detail=detail,
        retryable=retryable,
        node_id=nid,
        node_name=name,
    )


async def execute_single_node(
    node: dict[str, Any],
    user_input: str,
    intent: dict[str, Any],
    cancel_event: asyncio.Event | None = None,
    req_id: str | None = None,
) -> NodeExecResult:
    """
    执行单个节点：构建 Prompt + 委托 LLM + 异常分级响应。

    Args:
        node: 节点定义。非 dict 输入会降级为 failed（不抛）。
        user_input: 用户原始需求
        intent: 意图识别结果
        cancel_event: 用户取消信号，透传给 LLM 层
        req_id: 请求 ID；None 会用 "unknown" 兜底，空串保留

    Returns:
        NodeExecResult。status 只有三种：
          - "success"：LLM 返回成功
          - "failed"：LLM 返回失败 / prompt 构建失败 / node 结构非法
          - "timeout"：LLM 超时

    Raises:
        LLMCancelledError: 用户取消时向上抛，不在此处转成 status。
                           调用方（execute.py）负责捕获并转为 cancel_requested。

    ⚠️ detail 长度约定：
       未知异常分支的 detail 被整句截断到 _DETAIL_MAX_LEN 字符——
       前缀 "💥 未知异常: " + 类型名 + ": " 也计入长度上限，
       不会因为前缀占位而突破 200 字符的承诺。
       LLM 成功分支的 detail 为 LLM 原样输出，长度不可控——
       由 LLM 层负责约束，本模块不做二次截断。

    ⚠️ 日志时机：
       "[Node] ▶️ 开始执行节点..." 在 prompt 构建成功后才打（v2 起）。
       如果 prompt 构建失败，本节点不会留下"开始执行"日志。
    """
    rid = req_id if req_id is not None else "unknown"

    # ── 输入防御：node 非 dict 直接降级，不走 prompt / LLM ──
    if not isinstance(node, dict):
        logger.warning(
            "[Node] execute_single_node received non-dict node: %r (req=%s)",
            type(node).__name__, rid,
        )
        return _make_result(
            "?", "?",
            status="failed",
            detail=f"💥 非法节点结构: {type(node).__name__}",
            retryable=False,  # 结构问题重试无意义
        )

    # ✅ P5-2：node["id"] 的归一化推迟到 _make_result 构造时
    #    （由 NodeExecResult._normalize_node_id validator 承担）。
    #    这里只做"缺失时的默认值"兜底。
    nid = node.get("id", "?")
    # ✅ P4-3：用 _coerce_text 处理 name——
    #   - None → "" → 落到 f"步骤{nid}" 兜底
    #   - 空串 → 同样落到兜底（name 为空在 UI 无意义）
    #   - 其他类型 → str(...)
    name = _coerce_text(node.get("name")) or f"步骤{nid}"

    # meta 非 dict 防御：LLM 输出可能漂移为 str / list
    raw_meta = node.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    if raw_meta is not None and not isinstance(raw_meta, dict):
        logger.warning(
            "[Node] node[meta] expected dict, got %s; ignored (req=%s)",
            type(raw_meta).__name__, rid,
        )

    try:
        # ── 构建 Prompt（可能在 json.dumps / 模板渲染阶段抛）──
        # 放在 try 内是刻意的：这里失败也要降级为节点 failed，
        # 而不是穿透到 LangGraph 主循环。
        prompt = render_template(
            EXECUTE_NODE_PROMPT, rid,
            user_input=user_input,
            # ✅ P4-4：category 同用 _coerce_text——
            #   原写法 str(intent.get("category", "other")) 在
            #   intent["category"] = None 时会产出 "None" 字符串。
            category=_coerce_text(intent.get("category")) or "other",
            name=name,
            # ✅ P4-3：details 优先，detail 兜底；两者都为 None 时变空串。
            #   原写法 node.get("details", node.get("detail", "")) 在
            #   details 存在但为 None 时会返回 None，再被 str() 变成 "None"。
            detail=_coerce_text(node.get("details") or node.get("detail")),
            preconditions=json.dumps(meta.get("preconditions", []), ensure_ascii=False),
            postconditions=json.dumps(meta.get("postconditions", []), ensure_ascii=False),
        )

        logger.info("[Node] ▶️ 开始执行节点%s: %s (req=%s)", nid, name, rid)

        # ── 委托 LLM ──
        success, raw_detail = await execute_node_llm(
            prompt=prompt,
            req_id=rid,
            cancel_event=cancel_event,
            timeout=_hub.dev.LLM_NODE_TIMEOUT,
        )

    except LLMCancelledError:
        # 用户取消 → 向上抛出，触发图优雅停止。
        # 注意：这里刻意不返回 "cancelled" 状态，契约由调用方 catch 决定。
        logger.info("[Node] ⏹️ 节点%s [%s] 被用户取消 (req=%s)", nid, name, rid)
        raise

    except LLMTimeoutError as e:
        logger.warning("[Node] ⏰ 节点%s [%s] 最终超时: %s (req=%s)", nid, name, e, rid)
        return _make_result(
            nid, name,
            status="timeout",
            detail="⏰ 执行超时，请稍后重试",
            retryable=True,
        )

    except Exception as e:
        # 覆盖范围：json.dumps / render_template / LLM 层其他异常。
        # retryable=False 是保守选择：未知异常多为代码 bug，
        # 让前端不显示"重试"按钮，避免用户点了还是失败。
        logger.exception("[Node] 🔥 节点%s [%s] 未知异常 (req=%s)", nid, name, rid)
        return _make_result(
            nid, name,
            status="failed",
            # ✅ P4-2：整句截断——前缀 + 类型名 + 消息合计不超过 _DETAIL_MAX_LEN。
            #   原写法 str(e)[:_DETAIL_MAX_LEN] 只截消息，
            #   前缀不计入，实际最多约 230 字符，与 docstring 承诺不符。
            detail=(
                f"💥 未知异常: {type(e).__name__}: {str(e)}"
            )[:_DETAIL_MAX_LEN],
            retryable=False,
        )

    # ── 正常返回路径 ──
    # 类型防御：LLM 层未强制返回 str，这里降级以保证 NodeExecResult 契约。
    detail = _coerce_text(raw_detail)
    status: Literal["success", "failed"] = "success" if success else "failed"
    logger.info(
        "[Node] %s 节点%s [%s] 执行%s (req=%s)",
        "✅" if success else "❌", nid, name, status, rid,
    )
    return _make_result(
        nid, name,
        status=status,
        detail=detail,
        retryable=not success,
    )


__all__ = ["execute_single_node", "NodeExecResult"]
