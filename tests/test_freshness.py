"""测试数据新鲜度汇总。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from alphapilot.data.cache import MarketDataCache
from alphapilot.service import AlphaPilotService, _data_freshness_summary


def _seed_with_date(cache: MarketDataCache, symbol: str, latest: date) -> None:
    """往 cache 塞一条给 symbol 的 K 线，最后日期 = latest。"""
    cache.upsert_instrument(symbol, f"测试 {symbol}", "stock", "测试")
    days_needed = (date.today() - latest).days + 5
    if days_needed < 30:
        days_needed = 30
    dates = pd.bdate_range(end=latest, periods=days_needed)
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "trade_date": d.strftime("%Y-%m-%d"),
            "open": 10 + i * 0.1, "high": 10.5 + i * 0.1, "low": 9.5 + i * 0.1,
            "close": 10.2 + i * 0.1, "volume": 1000, "amount": 10000.0,
        })
    cache.upsert_bars(symbol, pd.DataFrame(rows), provider="sample")


class TestDataFreshnessSummary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _register(self, symbol: str, latest: date | None) -> None:
        """注册仪器并写入 fetch_status（模拟拉取过一次）。"""
        self.cache.upsert_instrument(symbol, f"测试 {symbol}", "stock", "测试")
        self.cache.record_symbol_fetch_status(
            symbol, f"测试 {symbol}", "sample", "SUCCESS",
            latest.isoformat() if latest else None, 0, None,
        )

    def test_empty_cache_shows_all_missing(self) -> None:
        self._register("300124.SZ", None)
        self._register("002230.SZ", None)
        service = AlphaPilotService(self.db)
        service.cache = self.cache
        summary = _data_freshness_summary(self._fetch_statuses())
        self.assertEqual(summary["lag_distribution"]["无数据"], 2)
        self.assertTrue(summary["is_stale"])

    def test_today_lag_counted(self) -> None:
        self._register("300124.SZ", date.today())
        service = AlphaPilotService(self.db)
        service.cache = self.cache
        summary = _data_freshness_summary(self._fetch_statuses())
        self.assertEqual(summary["lag_distribution"]["today"], 1)
        self.assertEqual(summary["oldest_lag_days"], 0)
        self.assertFalse(summary["is_stale"])

    def test_old_lag_is_stale(self) -> None:
        self._register("300124.SZ", date.today() - timedelta(days=10))
        service = AlphaPilotService(self.db)
        service.cache = self.cache
        summary = _data_freshness_summary(self._fetch_statuses())
        self.assertEqual(summary["lag_distribution"]["8+ 天"], 1)
        self.assertEqual(summary["oldest_lag_days"], 10)
        self.assertTrue(summary["is_stale"])

    def test_mixed_lag_buckets(self) -> None:
        today = date.today()
        self._register("300124.SZ", today)  # today
        self._register("002230.SZ", today - timedelta(days=2))  # 1-3 天
        self._register("601138.SH", today - timedelta(days=5))  # 4-7 天
        self._register("688256.SH", today - timedelta(days=15))  # 8+ 天
        service = AlphaPilotService(self.db)
        service.cache = self.cache
        summary = _data_freshness_summary(self._fetch_statuses())
        self.assertEqual(summary["lag_distribution"]["today"], 1)
        self.assertEqual(summary["lag_distribution"]["1-3 天"], 1)
        self.assertEqual(summary["lag_distribution"]["4-7 天"], 1)
        self.assertEqual(summary["lag_distribution"]["8+ 天"], 1)
        self.assertEqual(summary["oldest_lag_days"], 15)

    def _fetch_statuses(self) -> dict:
        return {item["symbol"]: item for item in self.cache.fetch_symbol_statuses()}


class TestDataStatusIntegration(unittest.TestCase):
    def test_data_status_includes_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            cache = MarketDataCache(db)
            # 注入 fetch_status（带 K 线避免 ensure_initialized 触发自动灌数据）
            from alphapilot.config import BENCHMARKS, WATCHLIST
            import pandas as pd
            for inst in [*BENCHMARKS.values(), *WATCHLIST]:
                cache.upsert_instrument(inst.symbol, inst.name, inst.asset_type, inst.sector)
                # 30 天的 sample K 线以绕过 has_bars 判定
                dates = pd.bdate_range(end=date.today() - timedelta(days=2), periods=30)
                rows = []
                for i, d in enumerate(dates):
                    rows.append({
                        "trade_date": d.strftime("%Y-%m-%d"),
                        "open": 10 + i * 0.1, "high": 10.5, "low": 9.5,
                        "close": 10.2, "volume": 1000, "amount": 10000.0,
                    })
                cache.upsert_bars(inst.symbol, pd.DataFrame(rows), provider="sample")
                cache.record_symbol_fetch_status(
                    inst.symbol, inst.name, "sample", "SUCCESS",
                    (date.today() - timedelta(days=2)).isoformat(), 0, None,
                )
            service = AlphaPilotService(db)
            service.cache = cache
            status = service.data_status()
            self.assertIn("freshness", status)
            # 所有 10 个都应是 1-3 天
            self.assertGreaterEqual(status["freshness"]["lag_distribution"]["1-3 天"], 5)
            self.assertEqual(status["freshness"]["oldest_lag_days"], 2)


if __name__ == "__main__":
    unittest.main()
