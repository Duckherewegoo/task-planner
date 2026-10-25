"""兼容层：evaluators 已迁移到 tests.evaluation.evaluators。

保留本文件的原因：
  - old_eval_suite 下的 crucible_eval / dataset_manager 等文件可能仍
    通过 `from .harness.evaluators import ...` 引用
  - 保留 re-export 让 abandon 目录的旧代码不至于因迁移而崩溃

新代码请勿引用本路径。正式路径：
  from .evaluation.evaluators import Evaluators, _tokenize

本文件将在 abandon 目录整体清理时一并删除。
"""
from .evaluation.evaluators import Evaluators, _tokenize

__all__ = ["Evaluators", "_tokenize"]
