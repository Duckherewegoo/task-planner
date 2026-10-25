"""
test_mongodb.py — MongoDB 数据层集成测试

⚠️ 默认 skip（pytestmark = integration）——需要真实 MongoDB。
   运行前：
       docker compose up -d mongodb
       # .env 里 MONGO_DB 必须改成 *_test 结尾（如 task_planner_test），
       # 否则整个文件 skip（防止误清空生产库）

   运行方式：
       pytest tests/test_package/test_mongodb.py -m integration -v

   覆盖：
       - client.py  连接生命周期（init / get / close 幂等）
       - tasks.py   CRUD + 状态更新 + 级联删除
       - plans.py   Plan CRUD
       - nodes.py   节点数组操作（update / reset / get）

   为什么不用 mongomock：
       mongomock 对 $ 位置操作符、$inc、$nin 支持有限，
       测 "nodes.$.status" 这类更新会失真。
       走真实 MongoDB 才能验证"行为正确"而非"mock 形状正确"。
"""

from __future__ import annotations

import asyncio
import time

import pytest

# ── 依赖缺失时整个文件 skip（不是 error，也不影响 CI）──
pymongo = pytest.importorskip("pymongo", reason="需要 pymongo 包")


# 整个文件标记 integration——日常 pytest 默认不跑
pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════════
# 环境探测 + 安全护栏（模块级，只跑一次）
# ═══════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module", autouse=True)
def _mongodb_env():
    """
    模块级前置检查（任一失败则整模块 skip）：
      1. MONGO_DB 必须以 _test 结尾——防止误清空生产库
      2. MongoDB 必须可达——不可达则 skip

    teardown：关闭连接，避免影响其他测试文件。
    """
    from task_planner.core.db import client as db_client
    from task_planner.infrastructure.cog import hub as _hub

    db_name = _hub.dev.MONGO_DB
    if not db_name.endswith("_test"):
        pytest.skip(
            f"MONGO_DB={db_name!r} 不以 '_test' 结尾——"
            f"集成测试仅对 *_test 库运行（防止污染生产数据）"
        )

    # ── 探测 MongoDB 可达性 ──
    try:
        from pymongo import MongoClient

        probe = MongoClient(
            host=_hub.dev.MONGO_HOST,
            port=_hub.dev.MONGO_PORT,
            serverSelectionTimeoutMS=2000,
        )
        probe.admin.command("ping")
        probe.close()
    except Exception as e:
        pytest.skip(f"MongoDB 不可达（{_hub.dev.MONGO_HOST}:{_hub.dev.MONGO_PORT}）：{e}")

    yield

    # ── teardown ──
    db_client.close_db()


# ═══════════════════════════════════════════════════════════════════
# 测试隔离：每个测试前后清空 collections
# ═══════════════════════════════════════════════════════════════════
@pytest.fixture
async def clean_db():
    """
    每个测试前清空 tasks / plans——保证互不污染。
    测试后也清空（防残留影响下个测试）。
    """
    from task_planner.core.db.client import get_db

    db = get_db()
    db["tasks"].delete_many({})
    db["plans"].delete_many({})
    yield db
    db["tasks"].delete_many({})
    db["plans"].delete_many({})


# ═══════════════════════════════════════════════════════════════════
# 测试数据工厂
# ═══════════════════════════════════════════════════════════════════
def make_plan_data(
    task_name: str = "测试计划",
    nodes: list | None = None,
    edges: list | None = None,
) -> dict:
    """
    构造一份符合 db 层最低要求的 plan_data。

    ⚠️ 具体 schema 由 db.schema.validate_plan 校验——
       本工厂只保证 key 存在。若 validate_plan 收紧，改这里即可。
    """
    return {
        "task_name": task_name,
        "description": "用于集成测试",
        "nodes": nodes
        if nodes is not None
        else [
            {"id": 0, "name": "步骤一", "details": "第一步"},
            {"id": 1, "name": "步骤二", "details": "第二步"},
            {"id": 2, "name": "步骤三", "details": "第三步"},
        ],
        "edges": edges
        if edges is not None
        else [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# client.py — 连接生命周期
# ═══════════════════════════════════════════════════════════════════
async def test_init_db_idempotent():
    """init_db 重复调用不重复建连——第二次是 no-op"""
    from task_planner.core.db.client import close_db, get_db, init_db

    close_db()
    init_db()
    db1 = get_db()
    init_db()
    db2 = get_db()
    assert db1 is db2


async def test_get_db_auto_init():
    """未显式 init 时 get_db 会自动 init"""
    from task_planner.core.db.client import close_db, get_db

    close_db()
    db = get_db()
    assert db is not None


async def test_close_db_idempotent():
    """close_db 多次调用不报错——幂等"""
    from task_planner.core.db.client import close_db, init_db

    init_db()
    close_db()
    close_db()  # 第二次静默返回，不抛


async def test_close_then_get_reinit():
    """close 后再次 get_db 能重建连接"""
    from task_planner.core.db.client import close_db, get_db, init_db

    init_db()
    get_db()
    close_db()
    db2 = get_db()
    assert db2 is not None


# ═══════════════════════════════════════════════════════════════════
# tasks.py — 创建
# ═══════════════════════════════════════════════════════════════════
async def test_create_task_with_plan_returns_docs(clean_db):
    """返回 (task_doc, plan_doc)，且 task_doc.plan_id 指向 plan_doc"""
    from task_planner.core.db import create_task_with_plan

    task_doc, plan_doc = await create_task_with_plan(
        raw_query="测试需求",
        intent_info={"category": "other", "summary": "测试"},
        plan_data=make_plan_data(),
    )

    assert task_doc["task_id"].startswith("task_")
    assert plan_doc["plan_id"].startswith("plan_")
    assert task_doc["plan_id"] == plan_doc["plan_id"]


async def test_create_task_with_plan_persists(clean_db):
    """创建后能通过 get_task 取回，且 _id 被 pop"""
    from task_planner.core.db import create_task_with_plan, get_task

    task_doc, _ = await create_task_with_plan(
        raw_query="测试需求",
        intent_info={},
        plan_data=make_plan_data(),
    )

    fetched = await get_task(task_doc["task_id"])
    assert fetched is not None
    assert fetched["raw_query"] == "测试需求"
    assert "_id" not in fetched


async def test_create_task_with_plan_invalid_sets_failed_status(clean_db):
    """valid=False 时 status=FAILED，valid=False"""
    from task_planner.core.db import create_task_with_plan
    from task_planner.infrastructure.constants import TASK_STATUS

    task_doc, _ = await create_task_with_plan(
        raw_query="无效需求",
        intent_info={},
        plan_data=make_plan_data(),
        valid=False,
    )
    assert task_doc["status"] == TASK_STATUS["FAILED"]
    assert task_doc["valid"] is False


async def test_create_direct_answer_task(clean_db):
    """直接回答任务：plan_id 为空、direct_response 有值、status=SUCCESS"""
    from task_planner.core.db import create_direct_answer_task, get_task
    from task_planner.infrastructure.constants import TASK_STATUS

    task_doc = await create_direct_answer_task(
        raw_query="今天天气",
        intent_info={},
        response="晴天",
    )
    assert task_doc["direct_response"] == "晴天"
    assert task_doc["plan_id"] == ""
    assert task_doc["status"] == TASK_STATUS["SUCCESS"]

    fetched = await get_task(task_doc["task_id"])
    assert fetched["direct_response"] == "晴天"


# ═══════════════════════════════════════════════════════════════════
# tasks.py — 查询
# ═══════════════════════════════════════════════════════════════════
async def test_get_task_not_found_returns_none(clean_db):
    from task_planner.core.db import get_task

    assert await get_task("nonexistent") is None


async def test_get_task_fills_defaults(clean_db):
    """get_task 补全 schema 默认值——防下游 KeyError"""
    from datetime import UTC, datetime

    from task_planner.core.db import get_task
    from task_planner.core.db.client import get_db

    # 直接插入一个只有最小字段的 task（不走 create_task_with_plan）
    db = get_db()
    now = datetime.now(UTC)
    db["tasks"].insert_one({"task_id": "task_min", "created_at": now})

    fetched = await get_task("task_min")
    assert fetched["raw_query"] == ""
    assert fetched["intent_info"] == {}
    assert fetched["plan_id"] == ""
    assert fetched["valid"] is True
    assert fetched["retry_count"] == 0


async def test_load_task_with_plan_happy_path(clean_db):
    from task_planner.core.db import create_task_with_plan, load_task_with_plan

    task_doc, plan_doc = await create_task_with_plan(
        raw_query="测试",
        intent_info={},
        plan_data=make_plan_data(),
    )
    result = await load_task_with_plan(task_doc["task_id"])
    assert result is not None
    assert result["task"]["task_id"] == task_doc["task_id"]
    assert result["plan"]["plan_id"] == plan_doc["plan_id"]
    assert "warning" not in result


async def test_load_task_with_plan_task_not_found(clean_db):
    from task_planner.core.db import load_task_with_plan

    assert await load_task_with_plan("nonexistent") is None


async def test_load_task_with_plan_without_plan(clean_db):
    """task 存在但 plan_id 为空（直接回答）→ plan=None, 无 warning"""
    from task_planner.core.db import create_direct_answer_task, load_task_with_plan

    task_doc = await create_direct_answer_task(
        raw_query="q",
        intent_info={},
        response="r",
    )
    result = await load_task_with_plan(task_doc["task_id"])
    assert result is not None
    assert result["plan"] is None
    assert "warning" not in result


async def test_load_task_with_plan_plan_lost_warns(clean_db):
    """task 有 plan_id 但 plan 被删了 → plan=None, warning='plan_lost'"""
    from task_planner.core.db import (
        create_task_with_plan,
        delete_plan,
        load_task_with_plan,
    )

    task_doc, plan_doc = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    await delete_plan(plan_doc["plan_id"])

    result = await load_task_with_plan(task_doc["task_id"])
    assert result["plan"] is None
    assert result.get("warning") == "plan_lost"


async def test_list_tasks_empty(clean_db):
    from task_planner.core.db import list_tasks

    assert await list_tasks() == []


async def test_list_tasks_sorted_desc(clean_db):
    """最新创建的在最前"""
    from task_planner.core.db import create_direct_answer_task, list_tasks

    ids = []
    for i in range(3):
        doc = await create_direct_answer_task(
            raw_query=f"q{i}",
            intent_info={},
            response="r",
        )
        ids.append(doc["task_id"])
        await asyncio.sleep(0.01)  # 保证 created_at 不同

    listed = await list_tasks()
    assert len(listed) == 3
    assert listed[0]["task_id"] == ids[-1]
    assert listed[-1]["task_id"] == ids[0]


async def test_list_tasks_uses_summary_if_present(clean_db):
    """intent_info.summary 优先作为 title"""
    from task_planner.core.db import create_direct_answer_task, list_tasks

    await create_direct_answer_task(
        raw_query="原始长查询内容",
        intent_info={"summary": "简短摘要"},
        response="r",
    )
    listed = await list_tasks()
    assert "简短摘要" in listed[0]["title"]


async def test_list_tasks_direct_answer_has_chat_emoji(clean_db):
    """无 plan 的任务：标题前缀 💬、has_plan=False"""
    from task_planner.core.db import create_direct_answer_task, list_tasks

    await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    listed = await list_tasks()
    assert "💬" in listed[0]["title"]
    assert listed[0]["has_plan"] is False


async def test_list_tasks_with_plan_has_clipboard_emoji(clean_db):
    """有 plan 的任务：标题前缀 📋、has_plan=True"""
    from task_planner.core.db import create_task_with_plan, list_tasks

    await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    listed = await list_tasks()
    assert "📋" in listed[0]["title"]
    assert listed[0]["has_plan"] is True


async def test_get_recent_tasks_returns_strings(clean_db):
    from task_planner.core.db import create_direct_answer_task, get_recent_tasks

    await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    recent = await get_recent_tasks()
    assert len(recent) == 1
    assert isinstance(recent[0], str)


# ═══════════════════════════════════════════════════════════════════
# tasks.py — 状态更新
# ═══════════════════════════════════════════════════════════════════
async def test_mark_task_running(clean_db):
    from task_planner.core.db import (
        create_direct_answer_task,
        get_task,
        mark_task_running,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    doc = await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    await mark_task_running(doc["task_id"])
    fetched = await get_task(doc["task_id"])
    assert fetched["status"] == TASK_STATUS["RUNNING"]


async def test_mark_task_success_from_running(clean_db):
    from task_planner.core.db import (
        create_direct_answer_task,
        get_task,
        mark_task_running,
        mark_task_success,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    doc = await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    await mark_task_running(doc["task_id"])
    await mark_task_success(doc["task_id"])
    fetched = await get_task(doc["task_id"])
    assert fetched["status"] == TASK_STATUS["SUCCESS"]


async def test_mark_task_success_skips_terminal(clean_db):
    """
    已是终态的任务再 mark_success 不改变状态——
    保护语义（$nin TERMINAL_STATUSES）验证。
    """
    from task_planner.core.db import (
        create_direct_answer_task,
        get_task,
        mark_task_success,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    doc = await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    # create_direct_answer_task 创建时 status=SUCCESS（终态）
    await mark_task_success(doc["task_id"])
    fetched = await get_task(doc["task_id"])
    assert fetched["status"] == TASK_STATUS["SUCCESS"]


async def test_mark_task_failed_sets_error_msg(clean_db):
    from task_planner.core.db import (
        create_direct_answer_task,
        get_task,
        mark_task_failed,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    doc = await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    await mark_task_failed(doc["task_id"], error="boom")
    fetched = await get_task(doc["task_id"])
    assert fetched["status"] == TASK_STATUS["FAILED"]
    assert fetched["error_msg"] == "boom"


async def test_mark_task_timeout(clean_db):
    from task_planner.core.db import (
        create_direct_answer_task,
        get_task,
        mark_task_timeout,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    doc = await create_direct_answer_task(raw_query="q", intent_info={}, response="r")
    await mark_task_timeout(doc["task_id"])
    fetched = await get_task(doc["task_id"])
    assert fetched["status"] == TASK_STATUS["TIMEOUT"]


# ═══════════════════════════════════════════════════════════════════
# tasks.py — 删除
# ═══════════════════════════════════════════════════════════════════
async def test_delete_task_removes_task_and_plan(clean_db):
    """删除任务时级联删除关联 Plan"""
    from task_planner.core.db import (
        create_task_with_plan,
        delete_task,
        get_plan,
        get_task,
    )

    task_doc, plan_doc = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    ok = await delete_task(task_doc["task_id"])
    assert ok is True
    assert await get_task(task_doc["task_id"]) is None
    assert await get_plan(plan_doc["plan_id"]) is None


async def test_delete_task_not_found(clean_db):
    from task_planner.core.db import delete_task

    assert await delete_task("nonexistent") is False


async def test_batch_delete_tasks(clean_db):
    from task_planner.core.db import batch_delete_tasks, create_direct_answer_task

    ids = []
    for _ in range(3):
        doc = await create_direct_answer_task(
            raw_query="q",
            intent_info={},
            response="r",
        )
        ids.append(doc["task_id"])

    n = await batch_delete_tasks(ids)
    assert n == 3


async def test_batch_delete_tasks_empty(clean_db):
    from task_planner.core.db import batch_delete_tasks

    assert await batch_delete_tasks([]) == 0


# ═══════════════════════════════════════════════════════════════════
# plans.py — Plan CRUD
# ═══════════════════════════════════════════════════════════════════
async def test_create_plan_returns_doc(clean_db):
    from datetime import UTC, datetime

    from task_planner.core.db import create_plan

    now = datetime.now(UTC)
    plan_doc = await create_plan("plan_test_1", make_plan_data(), True, now)
    assert plan_doc["plan_id"] == "plan_test_1"
    assert plan_doc["graph_title"] == "测试计划"
    assert len(plan_doc["nodes"]) == 3
    assert len(plan_doc["edges"]) == 2
    assert plan_doc["valid"] is True
    assert plan_doc["modified_count"] == 0


async def test_get_plan_not_found(clean_db):
    from task_planner.core.db import get_plan

    assert await get_plan("nonexistent") is None


async def test_get_plan_strips_id(clean_db):
    from datetime import UTC, datetime

    from task_planner.core.db import create_plan, get_plan

    now = datetime.now(UTC)
    await create_plan("plan_x", make_plan_data(), True, now)
    got = await get_plan("plan_x")
    assert "_id" not in got


async def test_delete_plan(clean_db):
    from datetime import UTC, datetime

    from task_planner.core.db import create_plan, delete_plan, get_plan

    now = datetime.now(UTC)
    await create_plan("plan_x", make_plan_data(), True, now)
    assert await delete_plan("plan_x") is True
    assert await get_plan("plan_x") is None
    # 已删——第二次返回 False
    assert await delete_plan("plan_x") is False


async def test_delete_plans_batch(clean_db):
    from datetime import UTC, datetime

    from task_planner.core.db import create_plan, delete_plans

    now = datetime.now(UTC)
    for i in range(3):
        await create_plan(f"plan_{i}", make_plan_data(), True, now)
    n = await delete_plans(["plan_0", "plan_1", "plan_2"])
    assert n == 3


async def test_delete_plans_empty(clean_db):
    from task_planner.core.db import delete_plans

    assert await delete_plans([]) == 0


# ═══════════════════════════════════════════════════════════════════
# nodes.py — 节点数组操作
# ═══════════════════════════════════════════════════════════════════
async def test_update_node_status_success(clean_db):
    """数组位置操作符 nodes.$ 更新正确节点"""
    from task_planner.core.db import (
        create_task_with_plan,
        get_node_status,
        update_node_status,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    ok = await update_node_status(
        task_doc["task_id"],
        1,
        TASK_STATUS["RUNNING"],
        details="执行中",
    )
    assert ok is True
    status = await get_node_status(task_doc["task_id"], 1)
    assert status == TASK_STATUS["RUNNING"]


async def test_update_node_status_does_not_touch_other_nodes(clean_db):
    """只更新目标节点，其他节点不受影响"""
    from task_planner.core.db import (
        create_task_with_plan,
        get_node_status,
        update_node_status,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    await update_node_status(task_doc["task_id"], 1, TASK_STATUS["RUNNING"])

    # 节点 0 和 2 应保持 PENDING
    assert await get_node_status(task_doc["task_id"], 0) == TASK_STATUS["PENDING"]
    assert await get_node_status(task_doc["task_id"], 2) == TASK_STATUS["PENDING"]


async def test_update_node_status_node_not_found(clean_db):
    from task_planner.core.db import create_task_with_plan, update_node_status

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    ok = await update_node_status(task_doc["task_id"], 999, 1)
    assert ok is False


async def test_update_node_status_task_not_found(clean_db):
    from task_planner.core.db import update_node_status

    ok = await update_node_status("nonexistent_task", 0, 1)
    assert ok is False


async def test_update_node_status_increments_modified_count(clean_db):
    """每次更新 $inc modified_count"""
    from datetime import UTC, datetime

    from task_planner.core.db import create_plan, update_node_status
    from task_planner.core.db.client import get_db

    now = datetime.now(UTC)
    await create_plan("plan_mc", make_plan_data(), True, now)
    # 手动构造 task 关联（走最小路径）
    db = get_db()
    db["tasks"].insert_one(
        {
            "task_id": "task_mc",
            "plan_id": "plan_mc",
            "status": 0,
            "created_at": now,
            "updated_at": now,
        }
    )

    await update_node_status("task_mc", 1, 1)

    plan = db["plans"].find_one({"plan_id": "plan_mc"})
    assert plan["modified_count"] == 1


async def test_reset_node_status(clean_db):
    """reset 将状态置 PENDING 并清 error_msg"""
    from task_planner.core.db import (
        create_task_with_plan,
        get_node_status,
        reset_node_status,
        update_node_status,
    )
    from task_planner.infrastructure.constants import TASK_STATUS

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    await update_node_status(
        task_doc["task_id"],
        1,
        TASK_STATUS["FAILED"],
        details="出错了",
    )
    assert await get_node_status(task_doc["task_id"], 1) == TASK_STATUS["FAILED"]

    ok = await reset_node_status(task_doc["task_id"], 1)
    assert ok is True
    assert await get_node_status(task_doc["task_id"], 1) == TASK_STATUS["PENDING"]


async def test_reset_node_status_node_not_found(clean_db):
    from task_planner.core.db import create_task_with_plan, reset_node_status

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    assert await reset_node_status(task_doc["task_id"], 999) is False


async def test_get_node_status_returns_none_for_missing(clean_db):
    from task_planner.core.db import create_task_with_plan, get_node_status

    task_doc, _ = await create_task_with_plan(
        raw_query="q",
        intent_info={},
        plan_data=make_plan_data(),
    )
    assert await get_node_status(task_doc["task_id"], 999) is None
    assert await get_node_status("nonexistent", 0) is None
