from __future__ import annotations

from typing import Optional

import pandas as pd

from alphapilot.strategy.trend20 import _last_on_or_before, add_indicators


class IndicatorCache:
    """缓存 add_indicators 的计算结果，避免在单次信号/回测会话中重复算同一份 bars。

    用法::

        cache = IndicatorCache()
        market_i = cache.get(market_bars, as_of=as_of)
        sector_i = cache.get(sector_bars, as_of=as_of)
        leader_i = cache.get(leader_bars, as_of=as_of)
        # 同一份 bars 重复 get 不会重算
    """

    def __init__(self) -> None:
        self._cache: dict[tuple, pd.DataFrame] = {}

    def get(self, bars: pd.DataFrame, as_of: Optional[str] = None) -> pd.DataFrame:
        """返回 bars 经过 add_indicators 后的 DataFrame，自动按 (行数, 最后日期) 缓存。"""
        if bars is None or bars.empty:
            return bars
        # 缓存键：行数 + 最后一行 trade_date + as_of
        # 行列结构变化时算作不同条目
        last_date = str(bars["trade_date"].iloc[-1]) if "trade_date" in bars.columns else None
        key = (len(bars), last_date, as_of)
        if key not in self._cache:
            self._cache[key] = add_indicators(_last_on_or_before(bars, as_of))
        return self._cache[key]

    def clear(self) -> None:
        """清空缓存（用于长会话中释放内存）。"""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
