"""evaluation — 任务规划质量评估器。

对外暴露：
  - Evaluators：静态方法集（accuracy / exact_match / check_acyclic 等）
  - _tokenize：中英混合分词（中文 bigram + 英文整词），供测试复用

设计取舍：
  评估器本身是生产代码而非测试工具——它读 task_planner 的 hub 配置、
  被主流程与离线评估共用。放在 src 下而非 tests 下，是为了让离线评估
  脚本也能直接用（test 目录本不该被生产代码 import）。

  evaluation — 测试层共用的评估器实现。

⚠️ 此目录只供 tests/ 内部使用，不被 src/task_planner 依赖。
  - 若未来 src 侧也需要评估器，请迁入 task_planner.evaluation 正式包
  - 若未来没有任何测试再引用它，可整体删除
"""

from .evaluators import Evaluators, _tokenize

__all__ = ["Evaluators", "_tokenize"]
