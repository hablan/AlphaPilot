"""Strategy engines."""

from alphapilot.strategy import base as _base
from alphapilot.strategy.base import (
    StrategyEngine,
    get_engine,
    list_engines,
    register_engine,
)
from alphapilot.strategy.trend20 import Trend20Engine, Trend20Settings

# 注册内置策略
register_engine("trend20", Trend20Engine)

# 把内部 _REGISTRY 也导出（供测试/插件使用）
_REGISTRY = _base._REGISTRY

__all__ = [
    "StrategyEngine",
    "Trend20Engine",
    "Trend20Settings",
    "get_engine",
    "list_engines",
    "register_engine",
    "_REGISTRY",
]
