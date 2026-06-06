from __future__ import annotations

import unittest
from typing import Optional

import pandas as pd

from alphapilot.models import Signal
from alphapilot.strategy import (
    Trend20Engine,
    Trend20Settings,
    get_engine,
    list_engines,
    register_engine,
)
from alphapilot.strategy.base import StrategyEngine


class MockEngine(StrategyEngine):
    """测试用：永远返回 NORMAL action。"""

    name = "mock_normal"

    def __init__(self, settings=None):
        self.settings = settings

    def evaluate(self, code, name, bars, market_bars, sector_bars, **kwargs) -> Signal:  # type: ignore[override]
        if bars is None or bars.empty:
            return Signal(
                code=code, name=name, signal_date="",
                signal_type="WATCH", action="SKIP", score=0,
                estimated_win_rate=0.0, blocked_reasons=["empty bars"],
            )
        return Signal(
            code=code, name=name, signal_date=str(bars["trade_date"].iloc[-1]),
            signal_type="BUY", action="NORMAL", score=50,
            estimated_win_rate=0.55, reasons=["mock 总是开仓"],
        )


class FailingEngine(StrategyEngine):
    """测试用：总是抛错。"""

    name = "mock_failing"

    def __init__(self, settings=None):
        pass

    def evaluate(self, **kwargs) -> Signal:  # type: ignore[override]
        raise RuntimeError("mock failure")


# ---------------------------------------------------------------------------
# StrategyEngine ABC 行为
# ---------------------------------------------------------------------------


class TestStrategyEngineABC(unittest.TestCase):
    def test_cannot_instantiate_abstract(self) -> None:
        with self.assertRaises(TypeError):
            StrategyEngine()  # type: ignore[abstract]

    def test_subclass_must_implement_evaluate(self) -> None:
        class Incomplete(StrategyEngine):
            name = "incomplete"

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_trend20_is_a_strategy_engine(self) -> None:
        self.assertTrue(issubclass(Trend20Engine, StrategyEngine))
        engine = Trend20Engine()
        self.assertEqual(engine.name, "trend20")


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


class TestEngineRegistry(unittest.TestCase):
    def test_default_engines_listed(self) -> None:
        engines = list_engines()
        self.assertIn("trend20", engines)

    def test_get_known_engine(self) -> None:
        engine = get_engine("trend20")
        self.assertIsInstance(engine, Trend20Engine)

    def test_get_unknown_engine_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            get_engine("nonexistent")
        self.assertIn("unsupported", str(ctx.exception))
        self.assertIn("trend20", str(ctx.exception))

    def test_register_new_engine(self) -> None:
        register_engine("mock_normal", MockEngine)
        try:
            engine = get_engine("mock_normal")
            self.assertIsInstance(engine, MockEngine)
            self.assertIn("mock_normal", list_engines())
        finally:
            # 清理
            from alphapilot.strategy import _REGISTRY
            _REGISTRY.pop("mock_normal", None)

    def test_register_non_subclass_raises(self) -> None:
        class NotAnEngine:
            name = "nope"

        with self.assertRaises(TypeError):
            register_engine("nope", NotAnEngine)  # type: ignore[arg-type]

    def test_register_empty_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            register_engine("", MockEngine)


# ---------------------------------------------------------------------------
# 端到端：mock 引擎产出 Signal
# ---------------------------------------------------------------------------


class TestMockEngine(unittest.TestCase):
    def setUp(self) -> None:
        from alphapilot.strategy import _REGISTRY
        self._original = dict(_REGISTRY)
        register_engine("mock_normal", MockEngine)
        register_engine("mock_failing", FailingEngine)

    def tearDown(self) -> None:
        from alphapilot.strategy import _REGISTRY
        _REGISTRY.clear()
        _REGISTRY.update(self._original)

    def _bars(self) -> pd.DataFrame:
        return pd.DataFrame({
            "trade_date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "open": [10.0, 10.1, 10.2],
            "high": [10.5, 10.6, 10.7],
            "low": [9.5, 9.6, 9.7],
            "close": [10.0, 10.2, 10.3],
            "volume": [100, 200, 300],
        })

    def test_mock_engine_returns_normal(self) -> None:
        engine = get_engine("mock_normal")
        signal = engine.evaluate(
            code="000001.SZ", name="测试",
            bars=self._bars(), market_bars=pd.DataFrame(), sector_bars=pd.DataFrame(),
        )
        self.assertEqual(signal.action, "NORMAL")
        self.assertEqual(signal.score, 50)
        self.assertEqual(signal.estimated_win_rate, 0.55)

    def test_mock_engine_handles_empty_bars(self) -> None:
        engine = get_engine("mock_normal")
        signal = engine.evaluate(
            code="000001.SZ", name="测试",
            bars=pd.DataFrame(), market_bars=pd.DataFrame(), sector_bars=pd.DataFrame(),
        )
        self.assertEqual(signal.action, "SKIP")
        self.assertIn("empty bars", signal.blocked_reasons)

    def test_failing_engine_raises(self) -> None:
        engine = get_engine("mock_failing")
        with self.assertRaises(RuntimeError) as ctx:
            engine.evaluate(
                code="000001.SZ", name="测试",
                bars=self._bars(), market_bars=pd.DataFrame(), sector_bars=pd.DataFrame(),
            )
        self.assertIn("mock failure", str(ctx.exception))


# ---------------------------------------------------------------------------
# 与现有 Trend20Engine 行为对齐
# ---------------------------------------------------------------------------


class TestTrend20ThroughRegistry(unittest.TestCase):
    def test_get_trend20_via_registry_evaluates(self) -> None:
        engine = get_engine("trend20", Trend20Settings())
        # 给一个明显不满足 65 天数据的输入，应返回 SKIP
        bars = pd.DataFrame({
            "trade_date": pd.bdate_range("2024-01-01", periods=10).strftime("%Y-%m-%d"),
            "open": [10.0] * 10, "high": [10.5] * 10, "low": [9.5] * 10,
            "close": [10.0] * 10, "volume": [100] * 10,
        })
        signal = engine.evaluate(
            code="000001.SZ", name="测试", bars=bars,
            market_bars=bars, sector_bars=bars,
        )
        self.assertEqual(signal.action, "SKIP")
        self.assertIn("数据不足", "".join(signal.blocked_reasons))


if __name__ == "__main__":
    unittest.main()
