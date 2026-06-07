"""refresh 返回 as_of + dashboard ETag + paper equity edge case 测试 (2026-06-07)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.service import AlphaPilotService


class RefreshResponseFieldsTest(unittest.TestCase):
    """#11: /api/refresh 返回 as_of / new_bar_count / new_latest_trade_date"""

    def test_incremental_update_returns_as_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 模拟调用 incremental_update(不真拉网络,只验证返回结构)
            result = service.incremental_update(provider="sample", universe="watchlist")
            # 字段存在
            self.assertIn("as_of", result)
            self.assertIn("new_bar_count", result)
            self.assertIn("new_latest_trade_date", result)
            # 旧字段也在
            self.assertIn("success_count", result)
            self.assertIn("skipped_count", result)


class PaperEquityCurveEdgeCaseTest(unittest.TestCase):
    """修预存 test_paper_equity_curve_after_paper_buy:
    当 sample provider 数据最新日期 < mark_date(today)时,curve 不应为 0
    """

    def test_paper_equity_curve_clamped_to_latest_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # mark_date = today,但 sample provider 数据最新可能 < today
            service.mark_trade("300124.SZ", "BUY", 100, mode="paper")
            result = service.paper_equity_curve()
            # 修复后: curve 不为空
            self.assertGreater(len(result["curve"]), 0)
            self.assertEqual(result["summary"]["trade_count"], 1)

    def test_paper_equity_curve_explicit_end_date(self) -> None:
        """显式传 end_date 应不被 clamp 覆盖,但 mark_date 也在范围内时正常返回"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            cache = service.cache.lightweight_cache_status()
            latest = cache.get("latest_trade_date")
            # 显式指定 mark_date = latest,end_date = latest,曲线至少有 1 点
            service.mark_trade("300124.SZ", "BUY", 100, mode="paper", mark_date=latest)
            result = service.paper_equity_curve(end_date=latest)
            self.assertGreater(len(result["curve"]), 0)


if __name__ == "__main__":
    unittest.main()
