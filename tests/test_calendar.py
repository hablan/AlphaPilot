"""测试 A 股交易日历。"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from alphapilot.data.calendar import (
    KNOWN_HOLIDAYS,
    is_market_open_on,
    is_trade_day,
    last_n_trade_days,
    next_trade_day,
    previous_trade_day,
    trade_days_between,
)


class TestIsTradeDay(unittest.TestCase):
    def test_normal_workday(self) -> None:
        # 2026-06-03 是周三
        self.assertTrue(is_trade_day(date(2026, 6, 3)))

    def test_weekend(self) -> None:
        # 2026-06-06 是周六
        self.assertFalse(is_trade_day(date(2026, 6, 6)))
        self.assertFalse(is_trade_day(date(2026, 6, 7)))  # 周日

    def test_known_holiday(self) -> None:
        # 2026-06-19 是端午
        self.assertTrue("2026-06-19" in KNOWN_HOLIDAYS)
        self.assertFalse(is_trade_day(date(2026, 6, 19)))

    def test_known_holiday_independent_of_weekday(self) -> None:
        # 元旦通常是周三
        self.assertFalse(is_trade_day(date(2026, 1, 1)))

    def test_makeup_day(self) -> None:
        # 2026-09-27 是周日但被设为调休补班
        # 先确认这是周日
        d = date(2026, 9, 27)
        self.assertEqual(d.weekday(), 6)  # 6 = 周日
        # 调休补班表里得有这个
        from alphapilot.data.calendar import KNOWN_MAKEUP_DAYS
        self.assertIn("2026-09-27", KNOWN_MAKEUP_DAYS)
        # 当日应该是交易日
        self.assertTrue(is_trade_day(d))


class TestIsMarketOpenOn(unittest.TestCase):
    def test_workday_trading_hours(self) -> None:
        # 2026-06-03 周三 10:00
        self.assertTrue(is_market_open_on(date(2026, 6, 3), "10:00"))

    def test_workday_lunch(self) -> None:
        self.assertFalse(is_market_open_on(date(2026, 6, 3), "12:00"))

    def test_workday_before_open(self) -> None:
        self.assertFalse(is_market_open_on(date(2026, 6, 3), "09:00"))

    def test_workday_after_close(self) -> None:
        self.assertFalse(is_market_open_on(date(2026, 6, 3), "15:30"))

    def test_holiday_never_open(self) -> None:
        # 2026-10-01 国庆
        self.assertFalse(is_market_open_on(date(2026, 10, 1), "10:00"))
        self.assertFalse(is_market_open_on(date(2026, 10, 1), "11:00"))

    def test_weekend_never_open(self) -> None:
        # 2026-06-06 周六
        self.assertFalse(is_market_open_on(date(2026, 6, 6), "10:00"))


class TestNextPreviousTradeDay(unittest.TestCase):
    def test_next_trade_day_from_weekend(self) -> None:
        # 2026-06-06 周六 -> 下个交易日 2026-06-08 周一
        self.assertEqual(next_trade_day(date(2026, 6, 6)), date(2026, 6, 8))

    def test_next_trade_day_from_holiday(self) -> None:
        # 2026-06-19 端午 -> 2026-06-22 周一
        self.assertEqual(next_trade_day(date(2026, 6, 19)), date(2026, 6, 22))

    def test_previous_trade_day_from_weekend(self) -> None:
        # 周六之前是周五
        self.assertEqual(previous_trade_day(date(2026, 6, 6)), date(2026, 6, 5))


class TestTradeDaysBetween(unittest.TestCase):
    def test_returns_trade_days_only(self) -> None:
        # 2026-06-01 周一到 2026-06-05 周五，6 月 1 日也是工作日
        days = trade_days_between(date(2026, 6, 1), date(2026, 6, 5))
        # 全是工作日
        self.assertEqual(len(days), 5)
        for d in days:
            self.assertEqual(d.weekday(), d.weekday())
            self.assertLess(d.weekday(), 5)

    def test_inverted_range_returns_empty(self) -> None:
        self.assertEqual(trade_days_between(date(2026, 6, 10), date(2026, 6, 1)), [])

    def test_excludes_holiday(self) -> None:
        # 2026-06-19 端午
        days = trade_days_between(date(2026, 6, 18), date(2026, 6, 22))
        # 6/18 周四, 6/22 周一；6/19 端午跳过，6/20、6/21 是周末
        self.assertNotIn(date(2026, 6, 19), days)
        self.assertIn(date(2026, 6, 18), days)
        self.assertIn(date(2026, 6, 22), days)


class TestLastNTradeDays(unittest.TestCase):
    def test_last_5_days(self) -> None:
        # 从 2026-06-05 周五倒数 5 天
        days = last_n_trade_days(date(2026, 6, 5), 5)
        self.assertEqual(len(days), 5)
        # 最后一天是 6/5
        self.assertEqual(days[-1], date(2026, 6, 5))

    def test_skips_weekend_and_holiday(self) -> None:
        # 从 2026-06-22 周一倒数 5 天
        # 6/22 周一, 6/19 端午跳过
        days = last_n_trade_days(date(2026, 6, 22), 5)
        self.assertEqual(len(days), 5)
        # 6/22, 6/18, 6/17, 6/16, 6/15
        self.assertEqual(days[-1], date(2026, 6, 22))
        self.assertNotIn(date(2026, 6, 19), days)
        self.assertNotIn(date(2026, 6, 20), days)
        self.assertNotIn(date(2026, 6, 21), days)


if __name__ == "__main__":
    unittest.main()
