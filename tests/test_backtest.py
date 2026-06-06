from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.config import BENCHMARKS, WATCHLIST
from alphapilot.backtest.engine import Trend20Backtester
from alphapilot.data.bootstrap import initialize_market_cache
from alphapilot.data.cache import MarketDataCache


class Trend20BacktestTest(unittest.TestCase):
    def test_backtest_returns_required_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            initialize_market_cache(cache, provider_name="sample", universe_name="watchlist", years=2)
            universe = {
                instrument.symbol: (instrument.name, cache.get_bars(instrument.symbol))
                for instrument in WATCHLIST
            }
            result = Trend20Backtester().run(
                universe,
                cache.get_bars(BENCHMARKS["market"].symbol),
                cache.get_bars(BENCHMARKS["sector"].symbol),
                cache.get_bars("601138.SH"),
            )

            summary = result["summary"]
            for key in ["trade_count", "win_rate", "profit_loss_ratio", "max_drawdown", "total_return"]:
                self.assertIn(key, summary)
            self.assertIn("共振信号", result["factor_win_rates"])
            self.assertIn("观察信号", result["factor_win_rates"])


if __name__ == "__main__":
    unittest.main()
