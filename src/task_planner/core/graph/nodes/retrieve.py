"""轻量经验检索：从 golden 里挑 top-k 相似案例，喂 planner 做 few-shot。

设计要点：
  - 中文分词：优先用 jieba 精确模式（可选依赖，未安装时降级为单字切）
  - 无 embedding / 向量库：纯 token 重叠 + Jaccard
  - 两级过滤：先按 category 硬过滤，再在同类内按相似度排序
  - 去重：同 task_name 只保留分最高的一条，避免 top-k 全是同质案例
  - 裁剪：丢掉评测字段（score / meta_review 等），detail 截断，控 prompt 体积
  - 开关：TP_ENABLE_RETRIEVAL=1 才启用；否则图里走直连，零开销
  - 惰性 + 线程安全：进程内首次调用才读 golden，加锁防重复加载

环境变量：
  TP_ENABLE_RETRIEVAL     "1" 启用（默认 "0"）
  TP_GOLDEN_PATH          覆盖 golden 路径（绝对或相对 CWD）
  TP_RETRIEVAL_TOP_K      返回条数（默认 3）
  TP_RETRIEVAL_STRICT_CAT "1" 严格按 category 过滤（默认 "1"）
"""

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from task_planner.core.graph.state import TaskState
from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.retrieve")

# 默认 golden 目录（与 tools.eval_suite.cli generate 的 --output 默认值对齐）
_DEFAULT_GOLDEN_DIR_REL = "eval_data"

# 裁剪上限：控制 few-shot 注入 prompt 后的体积
_DETAIL_MAX_LEN = 200
_DESC_MAX_LEN = 120
_NAME_MAX_LEN = 60

try:
    import jieba

    _HAS_JIEBA = True
except ImportError:  # pragma: no cover
    _HAS_JIEBA = False

# jieba 不可用时的兜底正则：英文按词、中文按单字
_TOKEN_RE_FALLBACK = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

# 极简中文 stopwords（虚词、代词、连接词）。
# 不追求全覆盖，只砍掉绝对无信号的高频字，降低 Jaccard 噪音。
_STOPWORDS_CN = frozenset(
    "的 了 和 是 就 都 而 及 与 着 或 在 有 我 你 他 她 它 这 那 之 为 以 于 上 下 中"
    " 也 很 到 说 要 去 会 能 可 但 又 还 被 把 让 给 从 向 对 由 因 所 若 如 即 则"
    " 不 没 未 无 非 啊 吗 呢 吧 呀 哦 嗯 哈".split()
)


# ---------- 工具函数 ----------


def _tokenize(text: Any) -> set[str]:
    """中文按词（jieba 精确模式）、英文按词切分。

    设计取舍：
      - jieba 能把"番茄"/"西红柿"/"年假"识别为完整词，语义准确性远超单字切
      - 未安装 jieba 时降级为"英文按词 + 中文按单字"，不阻塞主流程
      - 过滤中文单字与纯标点：单字多为虚词或被误切的字符，噪音大于信号
      - 过滤 stopwords：砍掉"的/了/是"这类高频虚词，让 Jaccard 聚焦实词

    输入任何类型都安全（None / 非 str 一律视为空）。
    """
    if text is None:
        return set()
    s = str(text).strip().lower()
    if not s:
        return set()

    if not _HAS_JIEBA:
        return set(_TOKEN_RE_FALLBACK.findall(s))

    words = jieba.lcut(s)
    tokens: set[str] = set()
    for w in words:
        w = w.strip()
        if not w:
            continue
        if not any(ch.isalnum() for ch in w):
            continue
        if len(w) == 1 and "\u4e00" <= w <= "\u9fff":
            continue
        if w in _STOPWORDS_CN:
            continue
        tokens.add(w)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _truncate(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def resolve_golden_path() -> Path:
    """按 env → CWD 相对路径 的顺序解析 golden 路径。

    公开接口：workflow.py 用它在图构建阶段判断 retrieve 节点是否应启用。
    路径解析逻辑的单一真相源——修改默认目录只需改 _DEFAULT_GOLDEN_DIR_REL。
    """
    env = os.environ.get("TP_GOLDEN_PATH")
    if env:
        p = Path(env).expanduser()
        if not p.exists():
            logger.error("[Retrieve] TP_GOLDEN_PATH 显式配置但不存在: %s（检索将返回空）", p)
        return p
    return (Path.cwd() / _DEFAULT_GOLDEN_DIR_REL).resolve()


# ---------- 案例裁剪 ----------


def _slim_case(case: dict) -> dict | None:
    """把 golden 用例裁成 few-shot 用的最小形态。返回 None 表示该条无效。"""
    if not isinstance(case, dict):
        return None

    user_input = case.get("user_input")
    if user_input is None:
        user_input = ""
    user_input = str(user_input).strip()
    if not user_input:
        return None

    plan = case.get("expected_plan")
    if not isinstance(plan, dict):
        plan = {}

    nodes: list[dict] = []
    for n in plan.get("nodes") or []:
        if not isinstance(n, dict) or n.get("id") is None:
            continue
        nodes.append(
            {
                "id": n.get("id"),
                "name": _truncate(n.get("name", ""), _NAME_MAX_LEN),
                "detail": _truncate(n.get("detail", ""), _DETAIL_MAX_LEN),
            }
        )

    valid_ids = {n["id"] for n in nodes}
    seen_edges: set[tuple] = set()
    edges: list[dict] = []
    for e in plan.get("edges") or []:
        if not isinstance(e, dict):
            continue
        f, t = e.get("from"), e.get("to")
        if f not in valid_ids or t not in valid_ids:
            continue
        key = (f, t)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edge: dict[str, Any] = {"from": f, "to": t}
        if e.get("label"):
            edge["label"] = _truncate(e["label"], _NAME_MAX_LEN)
        edges.append(edge)

    return {
        "user_input": user_input,
        "category": str(case.get("category") or "通用").strip() or "通用",
        "expected_plan": {
            "task_name": _truncate(plan.get("task_name", ""), _NAME_MAX_LEN),
            "description": _truncate(plan.get("description", ""), _DESC_MAX_LEN),
            "nodes": nodes,
            "edges": edges,
        },
    }


# ---------- 检索器 ----------


class ExampleRetriever:
    """从 golden 用例里按 (category, 文本相似度) 取 top-k。"""

    def __init__(
        self,
        golden_path: Path | None = None,
        top_k: int = 3,
        enforce_category: bool = True,
    ):
        self.top_k = max(1, int(top_k))
        self.enforce_category = bool(enforce_category)
        self.golden_path = golden_path or resolve_golden_path()
        self._examples: list[dict] = []
        self._tokens: list[set[str]] = []
        self._categories: list[str] = []
        self._plan_names: list[str] = []

        if self.golden_path.exists():
            self._load(self.golden_path)
        else:
            logger.warning("[Retrieve] golden 不存在: %s（检索将返回空）", self.golden_path)

    def _load(self, path: Path) -> None:
        """兼容两种 golden 存储形态：
        - 目录（eval_suite.cli generate 的默认产物）：每个 case 一个 .json
        - 单文件 JSON 数组（手工整理或老格式）
        """
        if _HAS_JIEBA:
            # 预热 jieba：首次 lcut 加载词典 ~1-2s，提前在加载阶段付掉，
            # 避免第一个用户请求感知到延迟
            jieba.lcut("预热")

        cases: list[dict] = []

        try:
            if path.is_dir():
                files = sorted(path.glob("*.json"))
                if not files:
                    logger.warning("[Retrieve] golden 目录为空: %s", path)
                    return
                for f in files:
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        logger.exception("[Retrieve] 跳过损坏的 golden 文件: %s", f)
                        continue
                    if isinstance(data, dict):
                        cases.append(data)
                    elif isinstance(data, list):
                        cases.extend(d for d in data if isinstance(d, dict))
            elif path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    cases = [d for d in raw if isinstance(d, dict)]
                elif isinstance(raw, dict):
                    cases = [raw]
                else:
                    logger.warning(
                        "[Retrieve] golden 顶层不是 dict/list，忽略：%s",
                        type(raw).__name__,
                    )
                    return
            else:
                logger.warning("[Retrieve] golden 路径不存在: %s", path)
                return
        except Exception:
            logger.exception("[Retrieve] golden 加载失败: %s", path)
            return

        for item in cases:
            slim = _slim_case(item)
            if slim is None:
                continue
            self._examples.append(slim)
            self._tokens.append(_tokenize(slim["user_input"]))
            self._categories.append(slim["category"])
            self._plan_names.append(slim["expected_plan"]["task_name"])

        logger.info("[Retrieve] 载入 %d 条 golden（源：%s）", len(self._examples), path)

    def search(
        self,
        query: Any,
        category: str | None = None,
        top_k: int | None = None,
    ) -> list[dict]:
        k = self.top_k if top_k is None else max(0, int(top_k))
        if k == 0 or not self._examples:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        cat = (str(category).strip() if category is not None else "通用") or "通用"

        if self.enforce_category and cat != "通用":
            candidates = [i for i, c in enumerate(self._categories) if c == cat]
            if not candidates:
                logger.info("[Retrieve] category=%r 无同类案例，退回全量", cat)
                candidates = list(range(len(self._examples)))
        else:
            candidates = list(range(len(self._examples)))

        scored = sorted(
            ((_jaccard(q_tokens, self._tokens[i]), i) for i in candidates),
            key=lambda x: x[0],
            reverse=True,
        )

        seen_names: set[str] = set()
        picked: list[dict] = []
        for score, idx in scored:
            if score <= 0.0:
                break
            name = self._plan_names[idx] or f"__anon_{idx}"
            if name in seen_names:
                continue
            seen_names.add(name)
            picked.append(self._examples[idx])
            if len(picked) >= k:
                break

        logger.info(
            "[Retrieve] query=%r cat=%r 候选=%d 命中=%d",
            _truncate(query, 40),
            cat,
            len(candidates),
            len(picked),
        )
        return picked


# ---------- 进程级缓存（线程安全） ----------
_retriever: ExampleRetriever | None = None
_retriever_lock = threading.Lock()


def is_enabled() -> bool:
    return os.environ.get("TP_ENABLE_RETRIEVAL", "0") == "1"


def _build_retriever() -> ExampleRetriever:
    return ExampleRetriever(
        top_k=int(os.environ.get("TP_RETRIEVAL_TOP_K", "3")),
        enforce_category=os.environ.get("TP_RETRIEVAL_STRICT_CAT", "1") == "1",
    )


def get_retriever() -> ExampleRetriever | None:
    global _retriever
    if not is_enabled():
        return None
    if _retriever is not None:
        return _retriever
    with _retriever_lock:
        if _retriever is None:
            _retriever = _build_retriever()
    return _retriever


def reset_cache() -> None:
    """测试 / A-B 用：强制下次调用重读 golden 与环境变量。"""
    global _retriever
    with _retriever_lock:
        _retriever = None


# ---------- 图节点 ----------


async def retrieve_node(
    state: TaskState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """图节点：把相似案例写进 state["retrieved_examples"]。

    未启用 / 无命中时返回空 list，下游 plan 节点按"无案例"分支处理。

    Args:
        state:  全局状态。读 state["user_input"] 与 state["intent"]["category"]。
        config: LangGraph 节点签名约定保留。当前未使用。

    Returns:
        {"retrieved_examples": list[dict]}——未启用时为空 list。

    任何异常都降级为空，绝不阻断主流程。
    """
    try:
        r = get_retriever()
        if r is None:
            return {"retrieved_examples": []}

        intent = state.get("intent")
        category = intent.get("category") if isinstance(intent, dict) else None

        hits = r.search(
            query=state.get("user_input", ""),
            category=category,
        )
        return {"retrieved_examples": hits}
    except Exception:
        logger.exception("[Retrieve] retrieve_node 异常，降级为空")
        return {"retrieved_examples": []}
