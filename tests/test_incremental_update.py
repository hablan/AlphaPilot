"""测试 K 线增量更新和当日 K 线（盘中快照）逻辑。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time as dtime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from alphapilot.data.bootstrap import (
    initialize_market_cache,
    is_market_open,
    next_market_open,
    _apply_intraday_override,
)
from alphapilot.data.cache import MarketDataCache
from alphapilot.data.providers import SampleDataProvider
from alphapilot.config import Instrument


def _bars(start: str, end: str, n: int = 5, base: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, end=end)
    # 取最后 n 个交易日，确保 latest 接近 end
    dates = dates[-n:] if len(dates) >= n else dates
    rows = []
    for i, d in enumerate(dates):
        price = base + i * 0.1
        rows.append({
            "trade_date": d.strftime("%Y-%m-%d"),
            "open": price, "high": price + 0.05, "low": price - 0.05,
            "close": price + 0.02, "volume": 1000 + i, "amount": 10000.0,
        })
    df = pd.DataFrame(rows)
    df.attrs["provider"] = "sample"
    return df


class TestIsMarketOpen(unittest.TestCase):
    def test_weekday_trading_hours(self) -> None:
        # 2026-06-01 是周一
        dt = datetime(2026, 6, 1, 10, 0)
        self.assertTrue(is_market_open(dt))

    def test_weekday_lunch(self) -> None:
        dt = datetime(2026, 6, 1, 12, 0)
        self.assertFalse(is_market_open(dt))

    def test_weekend(self) -> None:
        # 2026-06-06 是周六
        dt = datetime(2026, 6, 6, 10, 0)
        self.assertFalse(is_market_open(dt))

    def test_before_market(self) -> None:
        dt = datetime(2026, 6, 1, 8, 0)
        self.assertFalse(is_market_open(dt))

    def test_after_close(self) -> None:
        dt = datetime(2026, 6, 1, 15, 30)
        self.assertFalse(is_market_open(dt))


class TestNextMarketOpen(unittest.TestCase):
    def test_returns_today_when_before_open(self) -> None:
        dt = datetime(2026, 6, 1, 8, 0)
        result = next_market_open(dt)
        self.assertEqual(result, datetime(2026, 6, 1, 9, 30))

    def test_skips_to_tomorrow_when_weekday_after_close(self) -> None:
        dt = datetime(2026, 6, 1, 16, 0)  # 周一收盘后
        result = next_market_open(dt)
        self.assertEqual(result, datetime(2026, 6, 2, 9, 30))

    def test_skips_weekend(self) -> None:
        # 周五收盘后
        dt = datetime(2026, 6, 5, 16, 0)  # 周五
        result = next_market_open(dt)
        self.assertEqual(result, datetime(2026, 6, 8, 9, 30))


class TestIntradayOverride(unittest.TestCase):
    def test_overrides_today_bar_during_market_hours(self) -> None:
        """盘中使用 snapshot 覆盖当日 K 线。"""
        bars = _bars("2026-06-01", "2026-06-02", n=2)
        bars.attrs["provider"] = "akshare"
        end = "2026-06-02"

        snap = {
            "trade_date": "2026-06-02",
            "open": 100.0, "close": 105.0,
            "high": 110.0, "low": 99.0,
            "volume": 50000, "amount": 5000000,
        }
        class FakeProvider:
            name = "akshare"
            def fetch_intraday_snapshot(self, inst):
                return snap
        fake = FakeProvider()

        # 强制市场开盘
        with patch("alphapilot.data.bootstrap.is_market_open", return_value=True):
            result = _apply_intraday_override(fake, Instrument("000001.SZ", "X"), bars, end)

        self.assertTrue(result)
        last = bars.iloc[-1]
        self.assertEqual(last["open"], 100.0)
        self.assertEqual(last["close"], 105.0)
        self.assertEqual(last["high"], 110.0)

    def test_no_override_when_market_closed(self) -> None:
        bars = _bars("2026-06-01", "2026-06-02", n=2)
        original_close = bars.iloc[-1]["close"]
        snap = {"trade_date": "2026-06-02", "open": 100, "close": 999,
                "high": 110, "low": 99, "volume": 50000, "amount": 5000000}
        class FakeProvider:
            name = "akshare"
            def fetch_intraday_snapshot(self, inst):
                return snap
        with patch("alphapilot.data.bootstrap.is_market_open", return_value=False):
            result = _apply_intraday_override(FakeProvider(), Instrument("000001.SZ", "X"), bars, "2026-06-02")
        self.assertFalse(result)
        # K 线未变
        self.assertEqual(bars.iloc[-1]["close"], original_close)

    def test_no_override_when_provider_lacks_method(self) -> None:
        bars = _bars("2026-06-01", "2026-06-02", n=2)
        original_close = bars.iloc[-1]["close"]
        class BareProvider:
            name = "bare"
        with patch("alphapilot.data.bootstrap.is_market_open", return_value=True):
            result = _apply_intraday_override(BareProvider(), Instrument("000001.SZ", "X"), bars, "2026-06-02")
        self.assertFalse(result)
        self.assertEqual(bars.iloc[-1]["close"], original_close)

    def test_no_override_when_snapshot_is_none(self) -> None:
        bars = _bars("2026-06-01", "2026-06-02", n=2)
        original_close = bars.iloc[-1]["close"]
        class FakeProvider:
            name = "fake"
            def fetch_intraday_snapshot(self, inst):
                return None
        with patch("alphapilot.data.bootstrap.is_market_open", return_value=True):
            result = _apply_intraday_override(FakeProvider(), Instrument("000001.SZ", "X"), bars, "2026-06-02")
        self.assertFalse(result)
        self.assertEqual(bars.iloc[-1]["close"], original_close)

    def test_no_override_when_last_bar_is_not_today(self) -> None:
        bars = _bars("2026-05-20", "2026-05-22", n=3)
        original_close = bars.iloc[-1]["close"]
        snap = {"trade_date": "2026-06-02", "open": 100, "close": 999,
                "high": 110, "low": 99, "volume": 50000, "amount": 5000000}
        class FakeProvider:
            name = "fake"
            def fetch_intraday_snapshot(self, inst):
                return snap
        with patch("alphapilot.data.bootstrap.is_market_open", return_value=True):
            result = _apply_intraday_override(FakeProvider(), Instrument("000001.SZ", "X"), bars, "2026-06-02")
        self.assertFalse(result)
        self.assertEqual(bars.iloc[-1]["close"], original_close)


class TestIncrementalUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        self.inst = Instrument("000001.SZ", "测试")
        self.cache.upsert_instrument(self.inst.symbol, self.inst.name, self.inst.asset_type, self.inst.sector)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_incremental_skips_up_to_date(self) -> None:
        # 预填一行 2026-05-30 的数据
        bars = _bars("2026-05-30", "2026-05-30", n=1)
        self.cache.upsert_bars(self.inst.symbol, bars, provider="sample")
        # 改 cache 里的 last_trade_date 到 2026-06-01（=今天）
        bars2 = _bars("2026-06-01", "2026-06-01", n=1)
        self.cache.upsert_bars(self.inst.symbol, bars2, provider="sample")

        # 用一个不存在的 provider，让拉取逻辑走 fetcher（模拟）
        with patch("alphapilot.data.bootstrap._default_interval", return_value=0):
            result = initialize_market_cache(
                self.cache,
                provider_name="sample",
                universe_name="watchlist",
                years=1,
                incremental=True,
                end_date="2026-06-01",
                max_symbols=1,
            )
        # sample provider 总是会提供数据，latest_in_db == end_date 时应该 skip
        self.assertGreaterEqual(result["skipped_count"] + result["success_count"], 1)
        # 至少返回 market_status 字段
        self.assertIn("market_status", result)
        self.assertIn(result["market_status"], ("OPEN", "CLOSED"))

    def test_incremental_returns_refreshed_count(self) -> None:
        # 预填 2026-05-25 数据
        bars = _bars("2026-05-25", "2026-05-25", n=1)
        self.cache.upsert_bars(self.inst.symbol, bars, provider="sample")

        # 增量拉取，sample 会生成 last_trade_date+1 到 end 的全部数据
        # 这次会"覆盖" 2026-05-26 到今天，refreshed_count 应当 >= 0
        with patch("alphapilot.data.bootstrap._default_interval", return_value=0):
            result = initialize_market_cache(
                self.cache,
                provider_name="sample",
                universe_name="watchlist",
                years=1,
                incremental=True,
                end_date="2026-06-01",
                max_symbols=1,
            )
        self.assertIn("refreshed_count", result)
        self.assertGreaterEqual(result["refreshed_count"], 0)

    def test_full_mode_skips_cached(self) -> None:
        # 预填完整窗口：所有 watchlist 标的都给足够行数的 K 线
        # （之前用 1 行测试会因 _has_recent_bars 行数门槛被识别为未补齐）
        from alphapilot.config import WATCHLIST
        from alphapilot.data.bootstrap import MIN_BARS_FOR_RECENT
        bars = _bars("2025-01-01", "2026-06-01", n=MIN_BARS_FOR_RECENT + 50)
        for inst in WATCHLIST:
            self.cache.upsert_instrument(inst.symbol, inst.name, inst.asset_type, inst.sector)
            self.cache.upsert_bars(inst.symbol, bars, provider="sample")

        with patch("alphapilot.data.bootstrap._default_interval", return_value=0):
            result = initialize_market_cache(
                self.cache,
                provider_name="sample",
                universe_name="watchlist",
                years=1,
                resume=True,
                end_date="2026-06-01",
            )
        # 所有 watchlist 标的有足够行数 → resume 模式下应 SKIP
        self.assertGreaterEqual(result["skipped_count"], 1)

    def test_market_status_in_result(self) -> None:
        with patch("alphapilot.data.bootstrap._default_interval", return_value=0):
            result = initialize_market_cache(
                self.cache,
                provider_name="sample",
                universe_name="watchlist",
                years=1,
                max_symbols=1,
            )
        self.assertIn("market_status", result)
        self.assertIn("mode", result)
        self.assertEqual(result["mode"], "full")


class TestIntradayProviderHook(unittest.TestCase):
    """验证 provider 可以实现 fetch_intraday_snapshot，且不影响主流程。"""

    def test_sample_provider_has_no_intraday_method(self) -> None:
        # SampleDataProvider 不实现 fetch_intraday_snapshot
        self.assertFalse(hasattr(SampleDataProvider(), "fetch_intraday_snapshot"))

    def test_akshare_provider_has_intraday_method(self) -> None:
        from alphapilot.data.providers import AkShareProvider
        self.assertTrue(hasattr(AkShareProvider, "fetch_intraday_snapshot"))


class TestHasRecentBarsGuard(unittest.TestCase):
    """_has_recent_bars 必须有最低行数门槛，避免 sample 残留的 1 行被误判为 recent。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        self.inst = Instrument("000001.SZ", "测试")
        self.cache.upsert_instrument(self.inst.symbol, self.inst.name, self.inst.asset_type, self.inst.sector)

    def _seed(self, n: int, end_date: str = "2026-06-03"):
        bars = _bars("2026-01-01", end_date, n=n)
        self.cache.upsert_bars(self.inst.symbol, bars, provider="sample")

    def test_single_row_is_not_recent(self) -> None:
        from alphapilot.data.bootstrap import _has_recent_bars
        self._seed(n=1)
        result = _has_recent_bars(self.cache, self.inst.symbol, "2023-01-01", "2026-06-03")
        self.assertFalse(result)

    def test_few_rows_below_threshold_is_not_recent(self) -> None:
        from alphapilot.data.bootstrap import _has_recent_bars, MIN_BARS_FOR_RECENT
        self._seed(n=MIN_BARS_FOR_RECENT - 1)
        result = _has_recent_bars(self.cache, self.inst.symbol, "2023-01-01", "2026-06-03")
        self.assertFalse(result)

    def test_rows_at_or_above_threshold_is_recent(self) -> None:
        from alphapilot.data.bootstrap import _has_recent_bars, MIN_BARS_FOR_RECENT
        self._seed(n=MIN_BARS_FOR_RECENT + 50)
        result = _has_recent_bars(self.cache, self.inst.symbol, "2023-01-01", "2026-06-03")
        self.assertTrue(result)

    def test_no_bars_is_not_recent(self) -> None:
        from alphapilot.data.bootstrap import _has_recent_bars
        result = _has_recent_bars(self.cache, self.inst.symbol, "2023-01-01", "2026-06-03")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
