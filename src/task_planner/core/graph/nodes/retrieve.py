"""轻量经验检索：从 golden 里挑 top-k 相似案例，喂 planner 做 few-shot。

设计要点：
  - 零依赖：不引 embedding / 向量库，纯 token 重叠 + Jaccard
  - 中文按【单字】切，英文数字按【词】切
    （Jaccard 对中文才有意义；整段当 token 等于检索器失效）
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
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from task_planner.infrastructure.logger_setup import get_logger

logger = get_logger("task_planner.retrieve")

# 英文/数字按词；中文按单字——不要用 \w+，否则整句中文变成 1 个 token
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

_DETAIL_MAX_LEN = 200
_DESC_MAX_LEN = 120
_NAME_MAX_LEN = 60

_DEFAULT_GOLDEN_REL = "eval_reports/golden.json"


# ---------- 工具函数 ----------
def _tokenize(text: Any) -> set[str]:
    """英文按词、中文按字切分。输入任何类型都安全。"""
    if text is None:
        return set()
    s = str(text).lower()
    if not s:
        return set()
    return set(_TOKEN_RE.findall(s))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _truncate(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _resolve_golden_path() -> Path:
    """按 env → CWD 相对路径 的顺序解析。找不到就返回默认路径（调用方会看到 not exists）。"""
    env = os.environ.get("TP_GOLDEN_PATH")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
        logger.warning("[Retrieve] TP_GOLDEN_PATH 指向不存在: %s", p)
    return (Path.cwd() / _DEFAULT_GOLDEN_REL).resolve()


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

    # nodes：无 id 的丢弃，字段全部裁短
    nodes: list[dict] = []
    for n in plan.get("nodes") or []:
        if not isinstance(n, dict) or n.get("id") is None:
            continue
        nodes.append({
            "id": n.get("id"),
            "name": _truncate(n.get("name", ""), _NAME_MAX_LEN),
            "detail": _truncate(n.get("detail", ""), _DETAIL_MAX_LEN),
        })

    # edges：from / to 必须都在 nodes 里，且不重复
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
        self.golden_path = golden_path or _resolve_golden_path()
        self._examples: list[dict] = []
        self._tokens: list[set[str]] = []
        self._categories: list[str] = []
        self._plan_names: list[str] = []

        if self.golden_path.exists():
            self._load(self.golden_path)
        else:
            logger.warning("[Retrieve] golden 不存在: %s（检索将返回空）", self.golden_path)

    def _load(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("[Retrieve] golden 解析失败: %s", path)
            return

        if not isinstance(raw, list):
            logger.warning("[Retrieve] golden 顶层不是 list，忽略：%s", type(raw).__name__)
            return

        for item in raw:
            slim = _slim_case(item)
            if slim is None:
                continue
            self._examples.append(slim)
            self._tokens.append(_tokenize(slim["user_input"]))
            self._categories.append(slim["category"])
            self._plan_names.append(slim["expected_plan"]["task_name"])

        logger.info("[Retrieve] 载入 %d 条 golden（源：%s）", len(self._examples), path)

    # ---------- 核心 ----------
    def search(
        self,
        query: Any,
        category: str | None = None,
        top_k: int | None = None,
    ) -> list[dict]:
        # 显式传 0 也要尊重语义
        k = self.top_k if top_k is None else max(0, int(top_k))
        if k == 0 or not self._examples:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        cat = (str(category).strip() if category is not None else "通用") or "通用"

        # --- 1. category 硬过滤 ---
        if self.enforce_category and cat != "通用":
            candidates = [i for i, c in enumerate(self._categories) if c == cat]
            if not candidates:
                logger.info("[Retrieve] category=%r 无同类案例，退回全量", cat)
                candidates = list(range(len(self._examples)))
        else:
            candidates = list(range(len(self._examples)))

        # --- 2. 相似度排序 ---
        scored = sorted(
            ((_jaccard(q_tokens, self._tokens[i]), i) for i in candidates),
            key=lambda x: x[0],
            reverse=True,
        )

        # --- 3. 同 task_name 去重 + 分数 0 早停 ---
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
            _truncate(query, 40), cat, len(candidates), len(picked),
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
        if _retriever is None:                     # double-check
            _retriever = _build_retriever()
    return _retriever


def reset_cache() -> None:
    """测试 / A-B 用：强制下次调用重读 golden 与环境变量。"""
    global _retriever
    with _retriever_lock:
        _retriever = None


# ---------- 图节点 ----------
async def retrieve(state: dict) -> dict:
    """图节点：把相似案例写进 state['retrieved_examples']。

    未启用 / 无命中时返回空 list，下游 plan 节点按"无案例"分支处理。
    注：首次调用会同步加载 golden（毫秒级）；若日后 golden 大到几十 MB，
    可把 r.search 包进 asyncio.to_thread 避免短暂阻塞事件循环。
    """
    r = get_retriever()
    if r is None:
        return {"retrieved_examples": []}

    if not isinstance(state, dict):
        return {"retrieved_examples": []}

    hits = r.search(
        query=state.get("user_input", ""),
        category=state.get("category"),
    )
    return {"retrieved_examples": hits}
