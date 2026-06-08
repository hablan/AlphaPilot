"""dashboard_summary 端点测试 (2026-06-07)

覆盖:
- /api/dashboard/summary 返回精简版字段
- summary 路径不调 signals() (cache 不会互相影响)
- summary 也走 TTL cache
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.service import AlphaPilotService


class DashboardSummaryTest(unittest.TestCase):
    """精简版 dashboard 端点"""

    def test_summary_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            s = service.dashboard_summary()
            # 必备字段
            self.assertIn("as_of", s)
            self.assertIn("benchmarks", s)
            self.assertIn("data_status", s)
            self.assertIn("metrics", s)
            self.assertIn("cache", s)
            # 精简版不应有这些
            self.assertNotIn("signals", s)
            self.assertNotIn("signals_grouped", s)
            self.assertNotIn("sector_ranking", s)
            self.assertNotIn("holding_risks", s)
            self.assertNotIn("performance_curve", s)
            self.assertNotIn("next_session", s)
            self.assertNotIn("portfolio", s)
            self.assertNotIn("freshness", s)

    def test_summary_caches_separately_from_full(self) -> None:
        """summary 和 full dashboard 用不同 cache key,互不影响"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            full = service.dashboard()
            summary = service.dashboard_summary()
            # full 有 signals,summary 没有
            self.assertIn("signals", full)
            self.assertNotIn("signals", summary)
            # 但 cache 都填了
            self.assertIsNotNone(service._ttl_cache.get("dashboard"))
            self.assertIsNotNone(service._ttl_cache.get("dashboard_summary"))

    def test_summary_is_faster_than_full(self) -> None:
        """summary 不算 signals/sector/holding/performance,理论耗时更短"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 跳过冷启缓存(冷启包含 initialize_data 初始化,不公平)
            service.dashboard()
            service.dashboard_summary()
            import time
            t0 = time.time()
            service.dashboard()
            full_t = time.time() - t0
            t0 = time.time()
            service.dashboard_summary()
            sum_t = time.time() - t0
            # 缓存命中,两者都应 < 50ms;summary 应 <= full
            self.assertLess(sum_t, 0.05, f"summary 缓存命中耗时 {sum_t*1000:.1f}ms 应 < 50ms")
            self.assertLess(full_t, 0.05, f"full 缓存命中耗时 {full_t*1000:.1f}ms 应 < 50ms")


if __name__ == "__main__":
    unittest.main()
