"""mocks.py — USE_MOCK_LLM=True 时的返回数据"""
from __future__ import annotations

from typing import Any


def mock_intent(req_id: str) -> dict[str, Any]:
    return {
        "needs_planning": True,
        "category": "cooking",
        "summary": "Mock: 任务解析完成",
        "complexity": "medium",
    }


def mock_plan(req_id: str) -> dict[str, Any]:
    return {
        "task_name": "Mock任务",
        "description": "Mock模式生成的计划",
        "nodes": [
            {"id": 1, "name": "准备食材", "details": "洗切西红柿和鸡蛋"},
            {"id": 2, "name": "炒鸡蛋", "details": "热油炒散鸡蛋盛出"},
            {"id": 3, "name": "炒西红柿", "details": "炒出汤汁加糖"},
            {"id": 4, "name": "合并出锅", "details": "倒入鸡蛋混合均匀"},
        ],
        "edges": [
            {"from": 1, "to": 2, "label": "食材就绪"},
            {"from": 2, "to": 3, "label": "鸡蛋炒好"},
            {"from": 3, "to": 4, "label": "西红柿出汁"},
        ],
    }


def mock_refine(req_id: str) -> dict[str, Any]:
    return {
        "name": "Mock: 执行核心步骤",
        "details": "1. 准备工具\n2. 执行操作\n3. 验证结果",
        "meta": {
            "preconditions": ["前置条件已满足"],
            "postconditions": ["预期结果达成"],
            "retry_policy": "失败后重试最多3次",
        },
    }


__all__ = ["mock_intent", "mock_plan", "mock_refine"]
