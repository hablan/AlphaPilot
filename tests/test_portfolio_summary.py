"""测试首页持仓总览和下一交易日计划。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from alphapilot.data.cache import MarketDataCache
from alphapilot.journal.store import JournalStore
from alphapilot.service import AlphaPilotService


def _seed_cache(cache: MarketDataCache) -> None:
    cache.upsert_instrument("300124.SZ", "汇川技术", "stock", "机器人")
    cache.upsert_instrument("002230.SZ", "科大讯飞", "stock", "AI")
    # 500 天的 K 线（避免 insufficient data）
    dates = pd.bdate_range("2024-12-01", periods=200)
    rows = []
    for i, d in enumerate(dates):
        price = 10 + i * 0.05
        rows.append({
            "trade_date": d.strftime("%Y-%m-%d"),
            "open": price, "high": price + 0.1, "low": price - 0.1,
            "close": price + 0.02, "volume": 1000 + i, "amount": 10000.0,
        })
    cache.upsert_bars("300124.SZ", pd.DataFrame(rows), provider="sample")
    cache.upsert_bars("002230.SZ", pd.DataFrame(rows), provider="sample")
    cache.upsert_instrument("000001.SH", "上证指数", "index", "市场")
    cache.upsert_instrument("399006.SZ", "创业板指", "index", "风格")
    cache.upsert_instrument("159770.SZ", "机器人 ETF", "etf", "机器人")
    cache.upsert_bars("000001.SH", pd.DataFrame(rows), provider="sample")
    cache.upsert_bars("399006.SZ", pd.DataFrame(rows), provider="sample")
    cache.upsert_bars("159770.SZ", pd.DataFrame(rows), provider="sample")


class TestPortfolioSummary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        _seed_cache(self.cache)
        self.service = AlphaPilotService(self.db)
        # 不在 setUp 中跑 ensure_initialized（依赖 universe 配置）
        self.service.cache = self.cache
        self.service.journal = JournalStore(self.cache)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_portfolio(self) -> None:
        result = self.service._portfolio_summary()
        self.assertEqual(result["position_count"], 0)
        self.assertEqual(result["total_market_value"], 0.0)
        self.assertEqual(result["total_cost"], 0.0)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["realized_pnl_count"], 0)

    def test_single_position(self) -> None:
        # 买入 1000 股 300124 @ 10.0
        self.service.journal.mark_trade("300124.SZ", "BUY", 1000, 10.0)
        result = self.service._portfolio_summary()
        self.assertEqual(result["position_count"], 1)
        self.assertGreater(result["total_market_value"], 0)
        self.assertEqual(result["positions"][0]["code"], "300124.SZ")
        self.assertEqual(result["positions"][0]["shares"], 1000)

    def test_realized_pnl_counts(self) -> None:
        # 买入 1000 @ 10
        self.service.journal.mark_trade("300124.SZ", "BUY", 1000, 10.0)
        # 卖出 1000 @ 11（盈利，note 不含"亏损"）
        self.service.journal.mark_trade("300124.SZ", "SELL", 1000, 11.0)
        result = self.service._portfolio_summary()
        self.assertEqual(result["position_count"], 0)  # 卖完
        self.assertEqual(result["realized_pnl_count"], 1)
        # realized_pnl 应 >= 0
        self.assertGreaterEqual(result["realized_pnl"], 0)

    def test_realized_pnl_loss_count(self) -> None:
        self.service.journal.mark_trade("300124.SZ", "BUY", 1000, 10.0)
        self.service.journal.mark_trade("300124.SZ", "SELL", 1000, 9.0, note="亏损 5%")
        result = self.service._portfolio_summary()
        self.assertEqual(result["position_count"], 0)
        self.assertEqual(result["realized_pnl_count"], 1)
        self.assertLessEqual(result["realized_pnl"], 0)


class TestNextSessionPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        _seed_cache(self.cache)
        self.service = AlphaPilotService(self.db)
        self.service.cache = self.cache
        self.service.journal = JournalStore(self.cache)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_next_trade_date(self) -> None:
        # 强制真实日期上下文
        with patch("alphapilot.service.date") as mock_date:
            from datetime import date as RealDate
            mock_date.today.return_value = RealDate(2026, 6, 3)  # 周三
            mock_date.side_effect = lambda *a, **k: RealDate(*a, **k)
            result = self.service._next_session_plan()
        self.assertEqual(result["next_trade_date"], "2026-06-03")  # 周三是交易日
        self.assertEqual(result["days_until"], 0)

    def test_next_session_from_weekend(self) -> None:
        with patch("alphapilot.service.date") as mock_date:
            from datetime import date as RealDate
            mock_date.today.return_value = RealDate(2026, 6, 6)  # 周六
            mock_date.side_effect = lambda *a, **k: RealDate(*a, **k)
            result = self.service._next_session_plan()
        self.assertEqual(result["next_trade_date"], "2026-06-08")  # 下周一
        self.assertEqual(result["days_until"], 2)


if __name__ == "__main__":
    unittest.main()
