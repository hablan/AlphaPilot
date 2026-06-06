from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from alphapilot.data.cache import MarketDataCache
from alphapilot.journal.store import JournalStore


class TestJournalStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        self.journal = JournalStore(self.cache)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- 基础写入 ------------------------------------------------------

    def test_mark_buy_returns_full_record(self) -> None:
        result = self.journal.mark_trade(
            code="000001.SZ",
            side="BUY",
            shares=1000,
            price=10.5,
        )
        self.assertIn("id", result)
        self.assertEqual(result["code"], "000001.SZ")
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["shares"], 1000)
        self.assertEqual(result["price"], 10.5)

    def test_mark_sell_lowercased_side_accepted(self) -> None:
        result = self.journal.mark_trade(
            code="000001.SZ",
            side="sell",  # 故意用小写
            shares=500,
            price=11.0,
        )
        self.assertEqual(result["side"], "SELL")

    # --- 参数校验 ------------------------------------------------------

    def test_invalid_side_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.mark_trade("000001.SZ", "HOLD", 100, 10.0)

    def test_zero_shares_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.mark_trade("000001.SZ", "BUY", 0, 10.0)

    def test_negative_shares_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.mark_trade("000001.SZ", "BUY", -1, 10.0)

    def test_zero_price_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.mark_trade("000001.SZ", "BUY", 100, 0)

    def test_negative_price_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.mark_trade("000001.SZ", "BUY", 100, -1.0)

    # --- 列表与排序 ---------------------------------------------------

    def test_list_marks_returns_inserted(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0, mark_date="2024-01-01")
        self.journal.mark_trade("000001.SZ", "SELL", 500, 11.0, mark_date="2024-01-02")
        marks = self.journal.list_marks()
        self.assertEqual(len(marks), 2)
        # 默认按 mark_date desc, id desc
        self.assertEqual(marks[0]["side"], "SELL")
        self.assertEqual(marks[1]["side"], "BUY")

    def test_list_marks_empty_initially(self) -> None:
        self.assertEqual(self.journal.list_marks(), [])

    # --- 持仓计算 ------------------------------------------------------

    def test_holdings_after_buy(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0)
        holdings = self.journal.holdings()
        self.assertIn("000001.SZ", holdings)
        self.assertEqual(holdings["000001.SZ"]["shares"], 1000)
        self.assertEqual(holdings["000001.SZ"]["cost"], 10.0)

    def test_holdings_weighted_average_cost(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0)
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 12.0)
        holdings = self.journal.holdings()
        self.assertEqual(holdings["000001.SZ"]["shares"], 2000)
        self.assertEqual(holdings["000001.SZ"]["cost"], 11.0)  # 加权平均

    def test_holdings_after_partial_sell(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0)
        self.journal.mark_trade("000001.SZ", "SELL", 400, 11.0)
        holdings = self.journal.holdings()
        self.assertEqual(holdings["000001.SZ"]["shares"], 600)
        self.assertEqual(holdings["000001.SZ"]["cost"], 10.0)  # 卖出不改变成本

    def test_holdings_after_full_sell_clears_position(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0)
        self.journal.mark_trade("000001.SZ", "SELL", 1000, 11.0)
        holdings = self.journal.holdings()
        self.assertEqual(holdings["000001.SZ"]["shares"], 0)
        self.assertEqual(holdings["000001.SZ"]["cost"], 0.0)

    def test_holdings_handles_oversell(self) -> None:
        # 卖出量超过持仓时不抛错，shares 归零
        self.journal.mark_trade("000001.SZ", "BUY", 100, 10.0)
        self.journal.mark_trade("000001.SZ", "SELL", 500, 11.0)
        holdings = self.journal.holdings()
        self.assertEqual(holdings["000001.SZ"]["shares"], 0)
        self.assertEqual(holdings["000001.SZ"]["cost"], 0.0)

    def test_holdings_tracks_multiple_symbols(self) -> None:
        self.journal.mark_trade("000001.SZ", "BUY", 1000, 10.0)
        self.journal.mark_trade("000002.SZ", "BUY", 500, 20.0)
        holdings = self.journal.holdings()
        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings["000001.SZ"]["shares"], 1000)
        self.assertEqual(holdings["000002.SZ"]["shares"], 500)

    # --- 附加字段 ------------------------------------------------------

    def test_mark_with_note_and_source_signal(self) -> None:
        result = self.journal.mark_trade(
            "000001.SZ",
            "BUY",
            1000,
            10.0,
            mark_date="2024-01-01",
            note="亏损 5%",
            source_signal_id="sig-001",
        )
        self.assertEqual(result["note"], "亏损 5%")
        marks = self.journal.list_marks()
        self.assertEqual(marks[0]["source_signal_id"], "sig-001")
        self.assertEqual(marks[0]["note"], "亏损 5%")

    # --- 损坏数据恢复 --------------------------------------------------

    def test_list_marks_survives_garbage_note(self) -> None:
        # 即便 note 字段是奇怪的字符也不应崩
        self.journal.mark_trade(
            "000001.SZ",
            "BUY",
            100,
            10.0,
            note="weird \x00 nulls & 'quotes' \"escape\"",
        )
        marks = self.journal.list_marks()
        self.assertEqual(len(marks), 1)
        self.assertIn("nulls", marks[0]["note"])

    # --- 并发写 --------------------------------------------------------

    def test_concurrent_writes_all_persist(self) -> None:
        # 简单并发：10 个线程各写 1 笔
        def writer(i: int) -> None:
            self.journal.mark_trade(
                "000001.SZ",
                "BUY",
                100,
                10.0 + i * 0.01,
                mark_date=f"2024-01-{(i % 28) + 1:02d}",
            )

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        marks = self.journal.list_marks()
        self.assertEqual(len(marks), 10)


if __name__ == "__main__":
    unittest.main()
