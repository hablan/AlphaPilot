from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.service import AlphaPilotService


class CacheStatusConsistencyTest(unittest.TestCase):
    """data_status() 与 cache_status() 报告的 bar_count / symbol_count 必须一致。

    回归测试：之前 lightweight_cache_status 从 symbol_fetch_status 聚合 row_count，
    会被 upsert 跳过，导致 dashboard 数字滞后于 CLI。
    """

    def test_bar_count_matches_cache_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)

            data = service.data_status()
            cache = service.cache.cache_status()

            self.assertEqual(data["bar_count"], cache["bar_count"])
            self.assertEqual(data["symbol_count"], cache["symbol_count"])

    def test_data_status_does_not_expose_estimated_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)

            data = service.data_status()
            self.assertNotIn("bar_count_estimated", data)


if __name__ == "__main__":
    unittest.main()
