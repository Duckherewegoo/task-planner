"""state.py — 初始状态构建 + 跳过规划判断

职责：
  1. should_skip_planning: 用正则判断"用户是否显式拒绝规划"——
     确定性的意图用确定性工具，不消耗 LLM 调用
  2. make_initial_state: 应用层构造入口——在 graph 层
     create_initial_state 的基础上叠加"业务判断"

⚠️ 与 graph/state.py 的 create_initial_state 的关系：
   本文件**不复制字段清单**——它调用 create_initial_state 并传入
   业务 override。字段定义的单一真相源在 create_initial_state。
   任何新字段只在那边加一次即可。

⚠️ 哨兵值 DIRECT_RESPONSE_PENDING：
   用于标记"用户显式拒绝规划，direct_answer_node 需要生成直答"。
   消费方（direct_answer_node）必须 import 本常量而不是重写字符串——
   避免字符串在多个文件里各写一遍导致漂移。
"""

from typing import Any

from task_planner.core.graph.state import TaskState, create_initial_state
from task_planner.infrastructure.logger_setup import get_logger
from task_planner.infrastructure.regexes import SKIP_PLANNING_PATTERNS

logger = get_logger(__name__)

# ✅ 哨兵值常量——消费者从本模块 import，不再各写字符串
DIRECT_RESPONSE_PENDING = "__DIRECT_RESPONSE_PENDING__"

# 注：原写法 `_SKIP_PLANNING_PATTERNS = SKIP_PLANNING_PATTERNS` 是纯 no-op 别名，
#     grep 确认无外部依赖后删除。若未来真需要"间接层"（如按环境切换 pattern 集），
#     应显式引入工厂函数而不是别名——别名无法表达"未来会变"这层意图。


def should_skip_planning(user_input: str) -> bool:
    """
    显式拒绝规划的正则拦截（同步，纯计算）。

    用户说"不用规划"、"直接告诉我"、"不要执行"之类的话——
    这是**可枚举的、明确的**意图，用正则即可覆盖。
    让 LLM 来判断这种是浪费。

    Args:
        user_input: 用户原始输入（类型契约是 str，不做运行时防御）

    Returns:
        True  — 命中任一 pattern，跳过规划
        False — 未命中，走正常规划流程

    ⚠️ 无 IO、无副作用、无异常。命中时打一条 info 日志便于统计
       "多少请求走了正则短路"。
    """
    for pattern in SKIP_PLANNING_PATTERNS:
        if pattern.search(user_input):
            logger.info("[Agent] 显式拒绝规划命中: %s", pattern.pattern)
            return True
    return False


def make_initial_state(user_input: str, thread_id: str) -> TaskState:
    """
    应用层构造入口——在 graph 层 create_initial_state 的基础上
    叠加"跳过规划"这一个业务判断。

    与 create_initial_state 的分工：
      - create_initial_state: 字段完整性（框架层）
      - make_initial_state:   业务策略（应用层）——目前只有"跳过规划"

    未来若要加新业务判断（如"简单模式跳过一个节点"），
    在本函数里加 override 即可，create_initial_state 不需要动。

    Args:
        user_input: 用户原始输入
        thread_id:  会话 ID

    Returns:
        TaskState：字段完整、业务策略已应用

    ⚠️ 字段清单不在此处复制——唯一的字段定义在 create_initial_state。
       本函数只负责传业务 override。
    """
    skip_planning = should_skip_planning(user_input)

    if skip_planning:
        logger.info("[Agent] 关键词/正则拦截：用户明确要求跳过规划")

    # ✅ 组合而非复制：字段定义只在 create_initial_state 一处。
    #    agent 层只写"它独有的 3 个业务 override"。
    #    schema_version 由 create_initial_state 决定（默认 1）。
    return create_initial_state(
        user_input,
        thread_id,
        needs_planning=not skip_planning,
        direct_response=DIRECT_RESPONSE_PENDING if skip_planning else "",
        steps=["跳过规划：用户显式拒绝"] if skip_planning else [],
    )


__all__ = [
    "DIRECT_RESPONSE_PENDING",
    "should_skip_planning",
    "make_initial_state",
]
