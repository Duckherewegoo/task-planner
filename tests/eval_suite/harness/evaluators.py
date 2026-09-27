"""
评估器集合：计算各种指标
"""

from typing import Dict, Any, List, Optional
import Levenshtein  # 可选，需要安装 python-Levenshtein
import re


class Evaluators:
    """静态评估方法集"""

    @staticmethod
    def accuracy(expected: str, actual: str) -> float:
        """简单字符串相似度（Levenshtein 距离归一化）"""
        if not expected and not actual:
            return 1.0
        if not expected or not actual:
            return 0.0
        dist = Levenshtein.distance(expected, actual)
        max_len = max(len(expected), len(actual))
        return 1.0 - (dist / max_len)

    @staticmethod
    def exact_match(expected: str, actual: str) -> bool:
        """精确匹配（忽略首尾空格）"""
        return expected.strip() == actual.strip()

    @staticmethod
    def contains_keyword(text: str, keywords: List[str]) -> bool:
        """检查文本是否包含任一关键词（忽略大小写）"""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    @staticmethod
    def plan_node_count_accuracy(expected_count: int, actual_nodes: List[Dict]) -> float:
        """节点数量准确性：1 - abs(exp-actual)/max(exp,1)"""
        actual_count = len(actual_nodes)
        if expected_count == 0 and actual_count == 0:
            return 1.0
        max_count = max(expected_count, 1)
        return 1.0 - (abs(expected_count - actual_count) / max_count)

    @staticmethod
    def check_acyclic(nodes: List[Dict], edges: List[Dict]) -> bool:
        """检查 DAG 是否无环（简单拓扑排序）"""
        from collections import defaultdict, deque

        graph = defaultdict(list)
        indegree = defaultdict(int)
        all_ids = set(str(n["id"]) for n in nodes)

        for e in edges:
            src = str(e["from"])
            tgt = str(e["to"])
            if src not in all_ids or tgt not in all_ids:
                continue
            graph[src].append(tgt)
            indegree[tgt] += 1

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
        """简单相关性：检查响应中是否包含输入中的关键词（词重叠比例）"""
        words_input = set(re.findall(r'\w+', input.lower()))
        words_response = set(re.findall(r'\w+', response.lower()))
        if not words_input:
            return 1.0
        overlap = len(words_input & words_response)
        return overlap / len(words_input)

    @staticmethod
    def latency_acceptance(elapsed: float, threshold: float = 10.0) -> bool:
        """延迟是否可接受（秒）"""
        return elapsed <= threshold
