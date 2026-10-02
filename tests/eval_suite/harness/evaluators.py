"""
评估器集合：计算各种指标

Changelog:
  ✅ P0-1：plan_node_count_accuracy 不再返回负分，结果恒在 [0, 1]
  ✅ P0-2：response_relevance 支持中文（bigram 切分），修复"中文恒为 0 分"
  ✅ P0-3：accuracy 改为词元级 Jaccard，不再用字符级 Levenshtein
  ✅ P1-1：移除硬 import Levenshtein（不再需要）
  ✅ P1-2：latency_acceptance 阈值从 config 读
  ✅ P1-3：exact_match 大小写不敏感 + 标点归一化
  ✅ P2-x：import 统一到顶部 / 分词函数抽到模块级 / 空串处理统一
"""

import re
import string
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from task_planner.infrastructure.cog import hub as _hub
LATENCY_ACCEPTANCE_THRESHOLD = _hub.dev.LATENCY_ACCEPTANCE_THRESHOLD


# ── 模块级常量：标点集合（中英文） ──
_PUNCT_CHARS = set(
    string.punctuation
    + "，。！？；：、""''（）【】《》〈〉「」『』〔〕·—…"
)


def _tokenize(text: str) -> set[str]:
    """
    通用分词：中文按字符 bigram，英文/数字按整词。

    为什么不用 \\w+：
      Python 的 \\w 会把整段中文当成一个 token，
      导致"西红柿炒鸡蛋"和"鸡蛋打散"完全无交集——中文相关性恒为 0。

    示例：
      "西红柿炒鸡蛋的做法" →
        {"西红", "红柿", "柿炒", "炒鸡", "鸡蛋", "蛋的", "的做", "做法"}

      "Hello World 123" →
        {"hello", "world", "123"}
    """
    tokens: set[str] = set()
    # 按"中文字符连续段"或"英文/数字连续段"切分
    for chunk in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text.lower()):
        if re.match(r'[\u4e00-\u9fff]', chunk):
            # 中文：bigram（单字单独处理）
            if len(chunk) == 1:
                tokens.add(chunk)
            else:
                for i in range(len(chunk) - 1):
                    tokens.add(chunk[i:i + 2])
        else:
            # 英文/数字：整词
            tokens.add(chunk)
    return tokens


class Evaluators:
    """静态评估方法集"""

    @staticmethod
    def accuracy(expected: str, actual: str) -> float:
        """
        准确性：基于词元级 Jaccard 相似度。

        为什么不用字符级 Levenshtein：
          - "西红柿炒鸡蛋" vs "鸡蛋炒西红柿" 字符 Levenshtein 距离 6/6 = 0 分
            但语义完全一致 → 系统性误判
          - 短答案一字之差会导致分数剧烈波动（"是的" vs "不是" 得 0 分）

        Jaccard = |A ∩ B| / |A ∪ B|，对语序不敏感，对同义改写友好。

        Returns:
            [0.0, 1.0] 之间的相似度
        """
        expected = (expected or "").strip()
        actual = (actual or "").strip()
        if not expected and not actual:
            return 1.0
        if not expected or not actual:
            return 0.0

        set_a = _tokenize(expected)
        set_b = _tokenize(actual)
        union = set_a | set_b
        if not union:
            return 0.0
        return len(set_a & set_b) / len(union)

    @staticmethod
    def exact_match(expected: str, actual: str) -> bool:
        """
        精确匹配（忽略首尾空格、大小写、常见中英文标点）。

        例：
          "Hello World" == "hello, world!"  → True
          "西红柿炒蛋。" == "西红柿炒蛋"    → True
        """
        def normalize(s: str) -> str:
            s = (s or "").strip().lower()
            return "".join(ch for ch in s if ch not in _PUNCT_CHARS)

        return normalize(expected) == normalize(actual)

    @staticmethod
    def contains_keyword(text: str, keywords: List[str]) -> bool:
        """检查文本是否包含任一关键词（忽略大小写）"""
        text_lower = (text or "").lower()
        return any(kw.lower() in text_lower for kw in keywords)

    @staticmethod
    def plan_node_count_accuracy(
        expected_count: int,
        actual_nodes_or_count: Any,
    ) -> float:
        """
        节点数量准确性，结果恒在 [0, 1]。

        ✅ P0-1 修复：
          原实现 `max(expected_count, 1)` 会导致：
            expected=0, actual=4 → 1 - 4/1 = -3.0  ← 负分！
          负分会把整体聚合指标严重拉低，让评估报告完全不可信。

          现在：
            - expected=0 且 actual=0       → 1.0（完美）
            - expected=0 或 actual=0       → 0.0（非负）
            - 其余用 max(exp, act) 做分母  → ∈ [0, 1]

        兼容两种入参：
          - actual_nodes_or_count 是 list → 取 len()
          - actual_nodes_or_count 是 int  → 直接用
        """
        if isinstance(actual_nodes_or_count, list):
            actual_count = len(actual_nodes_or_count)
        else:
            actual_count = int(actual_nodes_or_count)

        if expected_count == 0 and actual_count == 0:
            return 1.0
        if expected_count == 0 or actual_count == 0:
            return 0.0

        max_count = max(expected_count, actual_count)
        return 1.0 - (abs(expected_count - actual_count) / max_count)

    @staticmethod
    def check_acyclic(nodes: List[Dict], edges: List[Dict]) -> bool:
        """
        检查 DAG 是否无环（Kahn 拓扑排序，O(V+E)）。

        对"边指向不存在节点"的情况静默跳过——LLM 经常会生成这种幻觉边，
        应该视为正常噪声，而不是让整个评估失败。
        """
        graph: Dict[str, List[str]] = defaultdict(list)
        indegree: Dict[str, int] = defaultdict(int)
        all_ids = {str(n["id"]) for n in nodes}

        for e in edges:
            src = str(e["from"])
            tgt = str(e["to"])
            # 边指向不存在的节点 → 静默跳过
            if src not in all_ids or tgt not in all_ids:
                continue
            graph[src].append(tgt)
            indegree[tgt] += 1

        # 所有入度为 0 的节点入队
        queue = deque([nid for nid in all_ids if indegree[nid] == 0])
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in graph[node]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        return visited == len(all_ids)

    @staticmethod
    def response_relevance(response: str, input: str) -> float:
        """
        响应与输入的相关性（输入词元在响应中的覆盖率）。

        ✅ P0-2 修复：改用 _tokenize 支持中文 bigram。
          原实现用 \\w+ 会把整段中文当一个 token，
          导致中文场景下相关性恒为 0，评估完全失效。

        Returns:
            [0.0, 1.0]，表示"输入的关键词有多少出现在响应里"
        """
        if not input:
            return 1.0

        tokens_input = _tokenize(input)
        tokens_response = _tokenize(response)

        if not tokens_input:
            return 1.0
        return len(tokens_input & tokens_response) / len(tokens_input)

    @staticmethod
    def latency_acceptance(
        elapsed: float,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        延迟是否可接受（秒）。

        ✅ P1-2 修复：阈值从 config 读，不再硬编码 10 秒。

        Args:
            elapsed: 实际耗时（秒）
            threshold: 自定义阈值；None 时用 config.LATENCY_ACCEPTANCE_THRESHOLD
        """
        if threshold is None:
            threshold = LATENCY_ACCEPTANCE_THRESHOLD
        return elapsed <= threshold
