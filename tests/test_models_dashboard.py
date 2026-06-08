"""Signal 模型层重构测试 (2026-06-07)

覆盖:
- to_dashboard_signal() 字段白名单和 is_user_pick
- Signal.from_dict() 从全量 dict 提取核心字段
- Signal.to_dashboard_dict() 实例方法
- DASHBOARD_SIGNAL_FIELDS 集合维护
"""
from __future__ import annotations

import unittest

from alphapilot.models import (
    DASHBOARD_SIGNAL_FIELDS,
    Signal,
    to_dashboard_signal,
)


class ToDashboardSignalTest(unittest.TestCase):
    """to_dashboard_signal() 纯函数"""

    def test_returns_only_dashboard_fields(self) -> None:
        """输出字段必须在 DASHBOARD_SIGNAL_FIELDS 白名单内,不带 entry/exit/board"""
        full = {
            "code": "601138.SH", "name": "工业富联", "sector": "算力", "score": 100,
            "action": "NORMAL", "signal_type": "BUY", "signal_date": "2026-06-07",
            "estimated_win_rate": 0.71,
            "reasons": ["金叉", "板块强"],
            "blocked_reasons": [],
            "gate_state": {"market_above_ma20": True},
            "entry_signal": {"distance_ma20": 0.04},  # 应被剥离
            "exit_signal": {"below_ma20": False},  # 应被剥离
            "pnl_pct": None, "holding_shares": 0, "cost_price": None,
            "last_price": 50.0, "change_pct": 0.01,
            "board": "沪深主板",  # 应被剥离
            "reason_text": "金叉;板块强",
        }
        result = to_dashboard_signal(full, is_user_pick=True)
        # 输出 key 都是白名单内
        for k in result.keys():
            self.assertIn(k, DASHBOARD_SIGNAL_FIELDS)
        # 不应出现 entry/exit/board
        self.assertNotIn("entry_signal", result)
        self.assertNotIn("exit_signal", result)
        self.assertNotIn("board", result)
        # 不应出现下划线字段
        for k in result.keys():
            self.assertFalse(k.startswith("_"), f"underscore field leaked: {k}")
        # is_user_pick
        self.assertTrue(result["is_user_pick"])

    def test_blocked_summary_first_reason(self) -> None:
        s = {"code": "X", "blocked_reasons": ["原因A", "原因B"]}
        result = to_dashboard_signal(s)
        self.assertEqual(result["blocked_summary"], "原因A")
        self.assertEqual(result["blocked_reasons"], ["原因A", "原因B"])

    def test_blocked_summary_empty_when_no_reasons(self) -> None:
        s = {"code": "X", "blocked_reasons": []}
        result = to_dashboard_signal(s)
        self.assertEqual(result["blocked_summary"], "")

    def test_handles_missing_optional_fields(self) -> None:
        s = {"code": "X"}
        result = to_dashboard_signal(s)
        self.assertEqual(result["code"], "X")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["gate_state"], {})
        self.assertEqual(result["blocked_summary"], "")
        self.assertFalse(result["is_user_pick"])

    def test_is_user_pick_false_by_default(self) -> None:
        result = to_dashboard_signal({"code": "X"})
        self.assertFalse(result["is_user_pick"])


class SignalFromDictTest(unittest.TestCase):
    """Signal.from_dict() 类方法"""

    def test_extracts_core_fields(self) -> None:
        full = {
            "code": "601138.SH", "name": "工业富联", "signal_date": "2026-06-07",
            "signal_type": "BUY", "action": "NORMAL", "score": 100,
            "estimated_win_rate": 0.71,
            "reasons": ["金叉"],
            "blocked_reasons": ["板块弱"],
            "gate_state": {"foo": "bar"},
            "entry_signal": {"x": 1},  # 不入 dataclass
            "exit_signal": {"y": 2},  # 不入 dataclass
            "pnl_pct": -0.05, "holding_shares": 100, "cost_price": 50.0,
            "last_price": 48.0,  # 不入 dataclass(only dashboard)
            "change_pct": 0.01,  # 不入 dataclass
        }
        sig = Signal.from_dict(full)
        self.assertEqual(sig.code, "601138.SH")
        self.assertEqual(sig.action, "NORMAL")
        self.assertEqual(sig.reasons, ["金叉"])
        self.assertEqual(sig.pnl_pct, -0.05)
        # entry/exit/last_price/change_pct 不入 dataclass
        self.assertFalse(hasattr(sig, "entry_signal") and sig.entry_signal)
        self.assertFalse(hasattr(sig, "last_price"))

    def test_handles_missing_fields_with_defaults(self) -> None:
        sig = Signal.from_dict({"code": "X"})
        self.assertEqual(sig.code, "X")
        self.assertEqual(sig.action, "SKIP")
        self.assertEqual(sig.score, 0)
        self.assertEqual(sig.reasons, [])


class SignalToDashboardDictTest(unittest.TestCase):
    """Signal 实例的 to_dashboard_dict()"""

    def test_returns_dashboard_dict(self) -> None:
        sig = Signal(
            code="601138.SH", name="工业富联",
            signal_date="2026-06-07", signal_type="BUY",
            action="NORMAL", score=100, estimated_win_rate=0.71,
            reasons=["金叉"], blocked_reasons=[],
            gate_state={"a": 1}, pnl_pct=None, holding_shares=0, cost_price=None,
        )
        result = sig.to_dashboard_dict(is_user_pick=False)
        self.assertEqual(result["code"], "601138.SH")
        self.assertEqual(result["action"], "NORMAL")
        self.assertEqual(result["reasons"], ["金叉"])
        self.assertFalse(result["is_user_pick"])
        # 应该是 19 字段(dashboard 字段)
        self.assertEqual(len(result), 19)


class DashboardFieldsWhitelistTest(unittest.TestCase):
    """DASHBOARD_SIGNAL_FIELDS 完整性测试"""

    def test_whitelist_is_frozenset(self) -> None:
        """白名单是 frozenset,防止误改"""
        self.assertIsInstance(DASHBOARD_SIGNAL_FIELDS, frozenset)

    def test_whitelist_contains_required_keys(self) -> None:
        """白名单必须含前端 dashboard 用到的所有 key"""
        required = {
            "code", "name", "sector", "score", "action", "reasons",
            "blocked_reasons", "blocked_summary", "reason_text",
            "gate_state", "last_price", "change_pct", "pnl_pct",
            "holding_shares", "cost_price", "signal_type", "signal_date",
            "estimated_win_rate", "is_user_pick",
        }
        self.assertTrue(required.issubset(DASHBOARD_SIGNAL_FIELDS))


if __name__ == "__main__":
    unittest.main()
