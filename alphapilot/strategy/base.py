from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

import pandas as pd

from alphapilot.models import Signal

if TYPE_CHECKING:
    # 仅做类型检查时引入，避免循环 import
    from alphapilot.strategy.trend20 import Trend20Settings


class StrategyEngine(ABC):
    """策略引擎抽象基类。

    任何"针对一只标的、给出一个 Signal"的策略都应实现::

        class MyEngine(StrategyEngine):
            name = "my_strategy"

            def evaluate(self, code, name, bars, market_bars, sector_bars, ...) -> Signal:
                ...
    """

    # 子类应设置的人类可读名称，用于 CLI/UI 展示
    name: str = ""

    @abstractmethod
    def evaluate(
        self,
        code: str,
        name: str,
        bars: pd.DataFrame,
        market_bars: pd.DataFrame,
        sector_bars: pd.DataFrame,
        leader_bars: Optional[pd.DataFrame] = None,
        market_indicators: Optional[pd.DataFrame] = None,
        sector_indicators: Optional[pd.DataFrame] = None,
        leader_indicators: Optional[pd.DataFrame] = None,
        as_of: Optional[str] = None,
        loss_streak: int = 0,
        holding_shares: int = 0,
        cost_price: Optional[float] = None,
    ) -> Signal:
        """对一只标的产出 Signal。

        所有 *_indicators 参数都是"预计算好的指标 DataFrame"，
        实现应当优先使用预计算结果，未提供时才自行计算。
        """
        raise NotImplementedError


# 策略注册表：name -> 引擎类
_REGISTRY: dict[str, type[StrategyEngine]] = {}


def register_engine(name: str, cls: type[StrategyEngine]) -> None:
    """注册一个新策略类（供 plugin / 第三方策略使用）。"""
    if not isinstance(name, str) or not name:
        raise ValueError("engine name must be a non-empty string")
    if not isinstance(cls, type) or not issubclass(cls, StrategyEngine):
        raise TypeError(f"{cls} must be a subclass of StrategyEngine")
    _REGISTRY[name] = cls


def get_engine(name: str, settings: Optional["Trend20Settings"] = None) -> StrategyEngine:
    """根据名称实例化一个策略。"""
    if name not in _REGISTRY:
        raise ValueError(
            f"unsupported strategy: {name}; available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](settings)


def list_engines() -> list[str]:
    """返回所有已注册的策略名称。"""
    return sorted(_REGISTRY)
