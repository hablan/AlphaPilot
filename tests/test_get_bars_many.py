"""回归测试：增量更新后 K 线不应被 latest CTE 错误折叠。

具体场景：同一个 symbol 的不同 trade_date 由不同 fetched_at 拉取时，
老的 latest CTE（按 symbol 取 max fetched_at）会丢掉历史日期的更新，
导致 get_bars_many 实际只返回 max fetched_at 时刻的少量行。
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from alphapilot.data.cache import MarketDataCache


def _bars(start: str, end: str) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, end=end)
    rows = []
    for i, d in enumerate(dates):
        price = 10 + i * 0.1
        rows.append({
            "trade_date": d.strftime("%Y-%m-%d"),
            "open": price, "high": price + 0.1, "low": price - 0.1,
            "close": price + 0.02, "volume": 1000, "amount": 10000.0,
        })
    return pd.DataFrame(rows)


class TestGetBarsManyMultiFetchedAt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        self.symbol = "000001.SZ"
        self.cache.upsert_instrument(self.symbol, "测试", "stock", "测试")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_one_row_per_trade_date_after_multi_fetch(self) -> None:
        """同一 symbol 的不同日期由不同 fetched_at 写入时，get_bars_many 应能拉回全部日期。"""
        # 模拟 3 次拉取：先抓 2 月，再抓 3 月（追加），最后再抓 4 月
        bars_feb = _bars("2026-02-01", "2026-02-10")  # 7 个工作日
        bars_mar = _bars("2026-03-01", "2026-03-10")  # 7 个工作日
        bars_apr = _bars("2026-04-01", "2026-04-05")  # 3 个工作日
        self.cache.upsert_bars(self.symbol, bars_feb, provider="sample")
        self.cache.upsert_bars(self.symbol, bars_mar, provider="sample")
        self.cache.upsert_bars(self.symbol, bars_apr, provider="sample")
        # 看每个 fetched_at 多少行
        with self.cache.connect() as conn:
            rows = conn.execute(
                "select fetched_at, count(*) as n from daily_bars where symbol=? group by fetched_at order by fetched_at",
                (self.symbol,),
            ).fetchall()
        for r in rows:
            print("  fetch_at:", r[0], "rows:", r[1])
        # get_bars_many 拉 200 行（理论上能拿全部）
        bars = self.cache.get_bars_many([self.symbol], limit_rows=200)
        df = bars[self.symbol]
        expected = len(bars_feb) + len(bars_mar) + len(bars_apr)
        self.assertEqual(len(df), expected, f"期望 {expected} 行，实际 {len(df)}：{df['trade_date'].tolist()}")
        # 应该包含 2/3/4 月的日期
        # 用周一-周五区段，避免 bdate_range 跳过周末导致日期对不上
        self.assertIn("2026-02-02", df["trade_date"].astype(str).tolist())
        self.assertIn("2026-03-02", df["trade_date"].astype(str).tolist())
        self.assertIn("2026-04-01", df["trade_date"].astype(str).tolist())

    def test_limit_rows_truncates_per_symbol(self) -> None:
        """limit_rows=5 时每个 symbol 保留最近 5 条。"""
        self.cache.upsert_bars(self.symbol, _bars("2026-02-01", "2026-02-28"), provider="sample")
        bars = self.cache.get_bars_many([self.symbol], limit_rows=5)
        df = bars[self.symbol]
        self.assertEqual(len(df), 5)
        # 最新 5 天
        self.assertEqual(df["trade_date"].iloc[-1], "2026-02-27")

    def test_multi_symbol_each_gets_limit_rows(self) -> None:
        """多标的时每个标独立 limit_rows，不是全局限制。"""
        self.cache.upsert_instrument("000002.SZ", "B", "stock", "测试")
        self.cache.upsert_bars("000001.SZ", _bars("2026-02-01", "2026-02-28"), provider="sample")
        self.cache.upsert_bars("000002.SZ", _bars("2026-02-01", "2026-02-28"), provider="sample")
        bars = self.cache.get_bars_many(["000001.SZ", "000002.SZ"], limit_rows=5)
        self.assertEqual(len(bars["000001.SZ"]), 5)
        self.assertEqual(len(bars["000002.SZ"]), 5)


if __name__ == "__main__":
    unittest.main()
