import pytest
from task_planner.eval_suite.golden.validator import validate_golden_plan

@pytest.mark.parametrize("plan, expected_reason", [
    # 1. 空节点
    ({"nodes": []}, "nodes 为空"),
    # 2. ID 缺失
    ({"nodes": [{"name": "A"}]}, "存在无 id 的节点"),
    # 3. ID 类型错（bool 是 int 的子类，你的代码防了这一手，很赞）
    ({"nodes": [{"id": True, "name": "A"}]}, "id 必须是 int"),
    # 4. ID 不连续
    ({"nodes": [{"id": 1, "name": "A"}, {"id": 3, "name": "B"}]}, "id 不连续"),
    # 5. 缺少名字
    ({"nodes": [{"id": 1}]}, "缺 name"),
    # 6. 引用不存在的节点
    ({"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], 
      "edges": [{"from": 1, "to": 3}]}, "边 to=3 引用不存在的节点"),
    # 7. 自环
    ({"nodes": [{"id": 1, "name": "A"}], 
      "edges": [{"from": 1, "to": 1}]}, "自环"),
    # 8. 重复边
    ({"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], 
      "edges": [{"from": 1, "to": 2}, {"from": 1, "to": 2}]}, "重复边"),
    # 9. 环检测（A->B->C->A）
    ({"nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}], 
      "edges": [{"from": 1, "to": 2}, {"from": 2, "to": 3}, {"from": 3, "to": 1}]}, "检测到环"),
])

def test_validate_golden_plan_failures(plan, expected_reason):
    is_valid, reason = validate_golden_plan(plan)
    assert is_valid is False
    assert expected_reason in reason

def test_validate_golden_plan_success():
    valid_plan = {
        "nodes": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        "edges": [{"from": 1, "to": 2}]
    }
    is_valid, reason = validate_golden_plan(valid_plan)
    assert is_valid is True
    assert reason == "OK"
