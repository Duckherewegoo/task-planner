"""
config_helpers.py — 配置读取辅助函数

设计原则：
  - 每次调用从 hub.dev 实时读取，不做模块级缓存（热重载友好）
  - 类型转换失败时返回 fallback，不抛异常（启动不崩）
  - fallback 值本身由调用方保证合理（如并发数 >= 1）
"""

from task_planner.infrastructure.cog import hub as _hub


def get_dev_int(key: str, fallback: int) -> int:
    """安全读取 hub.dev 的整数配置。

    Args:
        key: hub.dev 上的属性名
        fallback: 非法或缺失时的降级值

    Returns:
        解析后的整数；val < 1 时返回 fallback；
        属性不存在 / 转换失败也返回 fallback。
    """
    raw = getattr(_hub.dev, key, fallback)
    try:
        val = int(raw)
        return val if val >= 1 else fallback
    except ValueError, TypeError:
        return fallback


def get_dev_float(key: str, fallback: float) -> float:
    """安全读取 hub.dev 的浮点数配置。

    Args:
        key: hub.dev 上的属性名
        fallback: 非法或缺失时的降级值

    Returns:
        解析后的浮点数；val <= 0 时返回 fallback。
    """
    raw = getattr(_hub.dev, key, fallback)
    try:
        val = float(raw)
        return val if val > 0 else fallback
    except ValueError, TypeError:
        return fallback


def get_dev_bool(key: str, fallback: bool) -> bool:
    """安全读取 hub.dev 的布尔配置。

    支持字符串 "true"/"false"/"1"/"0"/"yes"/"no"（大小写不敏感），
    以及 int/float 非零即真。

    Args:
        key: hub.dev 上的属性名
        fallback: 非法或缺失时的降级值
    """
    raw = getattr(_hub.dev, key, fallback)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return fallback


__all__ = ["get_dev_int", "get_dev_float", "get_dev_bool"]
