"""后端数据同步 + 接口契约的回归测试 (2026-06-07)

覆盖:
- #1 dashboard 一次 cache_status,避免 race
- #2 fund_flow.status 枚举
- #4 Signal 精简版字段
- #5 不再泄露下划线字段,改为 is_user_pick / blocked_summary
- #6 quote() 返回 is_intraday 字段
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from alphapilot.service import AlphaPilotService, _is_stale


class DashboardCacheRaceTest(unittest.TestCase):
    """#1: dashboard() 应只读一次 cache_status,避免同请求内 race(2-4 行差异)"""

    def test_dashboard_cache_bar_count_matches_data_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            dash = service.dashboard()
            # cache 和 data_status 用同一份 cache_status,bar_count 必须严格一致
            self.assertEqual(dash["cache"]["bar_count"], dash["data_status"]["bar_count"])
            self.assertEqual(dash["cache"]["symbol_count"], dash["data_status"]["symbol_count"])


class FundFlowStatusEnumTest(unittest.TestCase):
    """#2: data_status.fund_flow.status 必须是 ok / failed / missing / stale 之一"""

    def test_fund_flow_status_field_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            data = service.data_status()
            self.assertIn("status", data["fund_flow"])
            self.assertIn(data["fund_flow"]["status"], {"ok", "failed", "missing", "stale"})

    def test_fund_flow_missing_when_no_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            # 不调 initialize_fund_flow_cache,fund_flow_count=0
            data = service.data_status()
            self.assertEqual(data["fund_flow"]["status"], "missing")

    def test_fund_flow_stale_when_latest_too_old(self) -> None:
        # 直接测试 _is_stale 工具函数
        old = (date.today() - timedelta(days=10)).isoformat()
        self.assertTrue(_is_stale(old, days=3))
        # 1 天前不算 stale
        recent = (date.today() - timedelta(days=1)).isoformat()
        self.assertFalse(_is_stale(recent, days=3))
        # 空 / 非法 -> 视为 stale
        self.assertTrue(_is_stale("", days=3))
        self.assertTrue(_is_stale("not-a-date", days=3))


class DashboardSignalsMinimalTest(unittest.TestCase):
    """#4 + #5: dashboard 的 signals_grouped 用精简版字段,不再泄露下划线 / 不需要 entry/exit"""

    def _make_signal(self, action: str = "NORMAL", reasons=None, blocked=None, code: str = "601138.SH") -> dict:
        return {
            "code": code, "name": "测试", "signal_date": "2026-06-07", "signal_type": "BUY",
            "action": action, "score": 100, "estimated_win_rate": 0.7,
            "reasons": reasons or ["个股金叉"], "blocked_reasons": blocked or [],
            "gate_state": {"market_above_ma20": True},
            "entry_signal": {"distance_ma20": 0.01},
            "exit_signal": {"below_ma20": False},
            "pnl_pct": None, "holding_shares": 0, "cost_price": None,
            "last_price": 50.0, "change_pct": 0.01, "sector": "测试", "board": "沪深主板",
            "reason_text": ";".join(reasons or []),
        }

    def test_no_underscore_fields_in_dashboard_signals(self) -> None:
        from alphapilot.service import _group_signals_for_dashboard
        signals = [
            self._make_signal("NORMAL", reasons=["金叉", "板块强"], code="601138.SH"),
            self._make_signal("EXIT_ALERT", blocked=["跌破MA20"], code="000786.SZ"),
        ]
        user_picks = [{"symbol": "601138.SH"}]
        grouped = _group_signals_for_dashboard(signals, user_picks)
        for group in grouped.values():
            for item in group:
                # 不再有 _user_pick / _blocked_short 这种 Python 内部命名
                self.assertNotIn("_user_pick", item)
                self.assertNotIn("_blocked_short", item)
                # 替代字段存在
                self.assertIn("is_user_pick", item)
                self.assertIn("blocked_summary", item)

    def test_is_user_pick_set_correctly(self) -> None:
        from alphapilot.service import _group_signals_for_dashboard
        signals = [
            self._make_signal(code="601138.SH"),  # 在自选
            self._make_signal(code="000001.SZ"),  # 不在自选
        ]
        grouped = _group_signals_for_dashboard(signals, [{"symbol": "601138.SH"}])
        all_items = [it for items in grouped.values() for it in items]
        by_code = {it["code"]: it for it in all_items}
        self.assertTrue(by_code["601138.SH"]["is_user_pick"])
        self.assertFalse(by_code["000001.SZ"]["is_user_pick"])

    def test_blocked_summary_first_reason(self) -> None:
        from alphapilot.service import _group_signals_for_dashboard
        signals = [self._make_signal("SKIP", blocked=["原因A", "原因B"])]
        grouped = _group_signals_for_dashboard(signals, [])
        skip = grouped["skip"][0]
        self.assertEqual(skip["blocked_summary"], "原因A")

    def test_no_entry_exit_or_board_in_dashboard_signals(self) -> None:
        """dashboard 不需要 entry_signal / exit_signal / board,精简掉"""
        from alphapilot.service import _group_signals_for_dashboard
        signals = [self._make_signal()]
        grouped = _group_signals_for_dashboard(signals, [])
        item = grouped["buy"][0]
        self.assertNotIn("entry_signal", item)
        self.assertNotIn("exit_signal", item)
        self.assertNotIn("board", item)
        # 但 reason_text 保留(dashboard 已有逻辑用)
        self.assertIn("reason_text", item)


class QuoteIntradayTest(unittest.TestCase):
    """#6: quote() 返回 is_intraday 字段;非盘中时用日线 fallback"""

    def test_quote_returns_is_intraday_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # provider=None 时走 fallback,is_intraday=False
            quote = service.quote("601138.SH")
            self.assertIn("is_intraday", quote)
            self.assertFalse(quote["is_intraday"])
            self.assertIn("has_data", quote)

    def test_quote_no_data_returns_clean_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            quote = service.quote("NOTEXIST.XY")
            self.assertFalse(quote["has_data"])
            self.assertIsNone(quote["last_price"])


if __name__ == "__main__":
    unittest.main()
