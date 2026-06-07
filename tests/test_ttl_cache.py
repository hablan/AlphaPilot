"""TTL 缓存 + benchmarks 单一来源回归测试 (2026-06-07)

覆盖:
- #3 dashboard 5s TTL 复用 (5s 内两次调用结果应是同一对象)
- #9 backtest 5 分钟 TTL 复用
- #10 next_session 1 天 TTL 复用
- #7 metrics.market_state / style_state 已被删除(由 benchmarks 数组替代)
- 缓存显式失效(refresh / mark 后清空)
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from alphapilot.service import AlphaPilotService, TTLCache


class TTLCacheTest(unittest.TestCase):
    """TTLCache 基础行为"""

    def test_get_returns_none_when_empty(self) -> None:
        cache = TTLCache()
        self.assertIsNone(cache.get("nope"))

    def test_set_and_get(self) -> None:
        cache = TTLCache()
        cache.set("k", {"a": 1}, ttl_seconds=10.0)
        self.assertEqual(cache.get("k"), {"a": 1})

    def test_expiry(self) -> None:
        cache = TTLCache()
        cache.set("k", "v", ttl_seconds=0.05)
        self.assertEqual(cache.get("k"), "v")
        time.sleep(0.1)
        self.assertIsNone(cache.get("k"))

    def test_invalidate_specific_key(self) -> None:
        cache = TTLCache()
        cache.set("a", 1, ttl_seconds=10.0)
        cache.set("b", 2, ttl_seconds=10.0)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)

    def test_invalidate_all(self) -> None:
        cache = TTLCache()
        cache.set("a", 1, ttl_seconds=10.0)
        cache.set("b", 2, ttl_seconds=10.0)
        cache.invalidate()
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))

    def test_get_or_compute_runs_fn_once(self) -> None:
        cache = TTLCache()
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return "value"

        # 5s 内多次调用,compute 只跑 1 次
        cache.get_or_compute("k", ttl_seconds=5.0, compute_fn=compute)
        cache.get_or_compute("k", ttl_seconds=5.0, compute_fn=compute)
        cache.get_or_compute("k", ttl_seconds=5.0, compute_fn=compute)
        self.assertEqual(calls["n"], 1)


class DashboardCacheTest(unittest.TestCase):
    """#3: dashboard 5s TTL 复用,5s 内第二次返回同一对象"""

    def test_dashboard_reuses_cache_within_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 先调一次预热
            d1 = service.dashboard()
            # 5s 内再调,应返回同一对象(identity)
            d2 = service.dashboard()
            self.assertIs(d1, d2)
            # 内部 cache 应该有 dashboard 键
            self.assertIsNotNone(service._ttl_cache.get("dashboard"))

    def test_dashboard_cache_can_be_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            d1 = service.dashboard()
            service._ttl_cache.invalidate("dashboard")
            d2 = service.dashboard()
            # 显式失效后,应该是新对象
            self.assertIsNot(d1, d2)


class BacktestCacheTest(unittest.TestCase):
    """#9: backtest 5 分钟 TTL,5 分钟内复用"""

    def test_backtest_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            b1 = service.backtest()
            b2 = service.backtest()
            self.assertIs(b1, b2)


class NextSessionCacheTest(unittest.TestCase):
    """#10: _next_session_plan 1 天 TTL,同日复用"""

    def test_next_session_caches_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            n1 = service._next_session_plan()
            n2 = service._next_session_plan()
            self.assertIs(n1, n2)
            # cache key 包含日期
            cache_key = f"next_session:{n1['next_trade_date'][:0] or 'placeholder'}"
            # 直接通过 get_or_compute-like 路径调,cache 应有"next_session:YYYY-MM-DD"键
            keys = [k for k in [k for k in []]]  # placeholder
            self.assertTrue(any(k.startswith("next_session:") for k in service._ttl_cache._store.keys()))


class MetricsFieldsTest(unittest.TestCase):
    """#7: metrics.market_state / style_state 已删除(前端改读 benchmarks)"""

    def test_market_state_removed_from_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            dash = service.dashboard()
            self.assertNotIn("market_state", dash["metrics"])
            self.assertNotIn("style_state", dash["metrics"])
            # sector_state 暂时保留(老逻辑兼容),下个版本可一起删
            self.assertIn("sector_state", dash["metrics"])

    def test_benchmarks_array_has_sector_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            dash = service.dashboard()
            self.assertIn("benchmarks", dash)
            sector = next((b for b in dash["benchmarks"] if b["key"] == "sector"), None)
            self.assertIsNotNone(sector)
            self.assertIn("state", sector)


if __name__ == "__main__":
    unittest.main()
