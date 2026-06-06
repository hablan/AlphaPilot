from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

import pandas as pd

from alphapilot.i18n import (
    EXIT_REASON_BELOW_MA20,
    EXIT_REASON_MAX_HOLD,
    EXIT_REASON_STOP_LOSS,
    EXIT_REASON_TAKE_PROFIT,
)
from alphapilot.models import Trade
from alphapilot.strategy.cache import IndicatorCache
from alphapilot.strategy.trend20 import Trend20Engine, Trend20Settings


# 回测输出 factor_win_rates 的人类可读标签（中文，从 i18n 引用）
FACTOR_LABEL_NORMAL = "共振信号"
FACTOR_LABEL_TRIAL = "观察信号"


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    shares_per_trade: int = 1000
    max_hold_days: int = 30
    fee_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005


class Trend20Backtester:
    def __init__(
        self,
        settings: Optional[Trend20Settings] = None,
        config: Optional[BacktestConfig] = None,
    ):
        self.settings = settings or Trend20Settings()
        self.config = config or BacktestConfig()
        self.engine = Trend20Engine(self.settings)

    def run(
        self,
        universe: Dict[str, tuple[str, pd.DataFrame]],
        market_bars: pd.DataFrame,
        sector_bars: pd.DataFrame,
        leader_bars: Optional[pd.DataFrame] = None,
    ) -> dict:
        trades: list[Trade] = []
        loss_streak = 0
        cooldown_until: Optional[pd.Timestamp] = None

        # 预计算基准/板块/龙头的指标（每只股票共享）
        indicator_cache = IndicatorCache()
        market_i_full = indicator_cache.get(market_bars)
        sector_i_full = indicator_cache.get(sector_bars)
        leader_i_full = indicator_cache.get(leader_bars) if leader_bars is not None and not leader_bars.empty else None

        for code, (name, bars) in universe.items():
            if len(bars) < 100:
                continue
            bars_i = indicator_cache.get(bars)
            # 预排序 trade_date 一次，后续内层循环用 searchsorted 切片
            bars_dates = bars_i["trade_date"].astype(str).to_numpy()
            market_dates = market_i_full["trade_date"].astype(str).to_numpy()
            sector_dates = sector_i_full["trade_date"].astype(str).to_numpy()
            leader_dates = leader_i_full["trade_date"].astype(str).to_numpy() if leader_i_full is not None else None
            for index in range(70, len(bars_i) - 2):
                as_of = str(bars_i.iloc[index]["trade_date"])
                as_ts = pd.Timestamp(as_of)
                if cooldown_until is not None and as_ts <= cooldown_until:
                    continue
                # O(log n) 切片代替 O(n) 布尔过滤
                market_slice = market_i_full.iloc[: _searchsorted_le(market_dates, as_of) + 1]
                sector_slice = sector_i_full.iloc[: _searchsorted_le(sector_dates, as_of) + 1]
                leader_slice = (
                    leader_i_full.iloc[: _searchsorted_le(leader_dates, as_of) + 1]
                    if leader_dates is not None
                    else None
                )
                signal = self.engine.evaluate(
                    code=code,
                    name=name,
                    bars=bars_i.iloc[: index + 1],
                    market_bars=market_slice,
                    sector_bars=sector_slice,
                    leader_bars=leader_slice,
                    loss_streak=loss_streak,
                )
                if signal.action not in {"NORMAL", "TRIAL"}:
                    continue
                exit_index, exit_reason = self._find_exit(bars_i, index + 1, signal.action)
                if exit_index <= index + 1:
                    continue
                entry = bars_i.iloc[index + 1]
                exit_ = bars_i.iloc[exit_index]
                entry_price = float(entry["open"]) * (1 + self.config.slippage_rate)
                exit_price = float(exit_["open"]) * (1 - self.config.slippage_rate)
                gross = exit_price / entry_price - 1
                cost = self.config.fee_rate * 2 + self.config.stamp_tax_rate
                pnl_pct = round(gross - cost, 4)
                trades.append(
                    Trade(
                        code=code,
                        name=name,
                        entry_date=str(entry["trade_date"]),
                        entry_price=round(entry_price, 3),
                        exit_date=str(exit_["trade_date"]),
                        exit_price=round(exit_price, 3),
                        action=signal.action,
                        shares=self.config.shares_per_trade,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                    )
                )
                if pnl_pct < 0:
                    loss_streak += 1
                else:
                    loss_streak = 0
                if loss_streak >= self.settings.cooldown_loss_count:
                    cooldown_until = pd.Timestamp(exit_["trade_date"]) + pd.offsets.BDay(self.settings.cooldown_days)
                    loss_streak = 0
                break

        return summarize_trades(trades)

    def _find_exit(self, bars_i: pd.DataFrame, entry_index: int, action: str) -> tuple[int, str]:
        entry_price = float(bars_i.iloc[entry_index]["open"])
        for idx in range(entry_index + 1, min(len(bars_i), entry_index + self.config.max_hold_days)):
            row = bars_i.iloc[idx]
            pnl = float(row["close"]) / entry_price - 1
            if pnl >= self.settings.take_profit_pct:
                return idx, EXIT_REASON_TAKE_PROFIT
            if pnl <= self.settings.stop_loss_pct:
                return idx, EXIT_REASON_STOP_LOSS
            if row["close"] < row["ma20"]:
                return idx, EXIT_REASON_BELOW_MA20
        return min(len(bars_i) - 1, entry_index + self.config.max_hold_days - 1), EXIT_REASON_MAX_HOLD


def summarize_trades(trades: Iterable[Trade]) -> dict:
    trade_list = list(trades)
    if not trade_list:
        return {
            "summary": {
                "trade_count": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_loss_ratio": 0.0,
                "max_drawdown": 0.0,
                "total_return": 0.0,
            },
            "trades": [],
            "factor_win_rates": {},
        }

    pnl_values = [trade.pnl_pct for trade in trade_list]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value <= 0]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in pnl_values:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    normal = [trade for trade in trade_list if trade.action == "NORMAL"]
    trial = [trade for trade in trade_list if trade.action == "TRIAL"]

    return {
        "summary": {
            "trade_count": len(trade_list),
            "win_rate": round(len(wins) / len(trade_list), 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_loss_ratio": round(abs(avg_win / avg_loss), 4) if avg_loss else 0.0,
            "max_drawdown": round(max_drawdown, 4),
            "total_return": round(equity - 1, 4),
        },
        "trades": [trade.to_dict() for trade in trade_list],
        "factor_win_rates": {
            FACTOR_LABEL_NORMAL: _win_rate(normal),
            FACTOR_LABEL_TRIAL: _win_rate(trial),
        },
    }


def _win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return round(sum(1 for trade in trades if trade.pnl_pct > 0) / len(trades), 4)


def _searchsorted_le(dates: "np.ndarray[str]", as_of: str) -> int:
    """返回 dates 中 <= as_of 的最后一个下标，O(log n)。

    假设 dates 已按升序排列。-1 表示没有匹配项。
    """
    import numpy as np

    idx = np.searchsorted(dates, as_of, side="right") - 1
    return int(idx)
