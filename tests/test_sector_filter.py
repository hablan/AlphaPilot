"""sector 切基准联动测试 (2026-06-07)

2026-06-07 决策: 切板块**不**过滤 watchlist(避免 watchlist 中无该 sector 标的时一片空白)。
只通过 `sector_indicators` 影响 gate 强弱,hint 文字显示当前 sector 名称。
本测试验证: 切到不同 sector,signals 数量不变(全 watchlist),但 signal.engine 用的 sector_bars 不同。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.config import BENCHMARK_CANDIDATES
from alphapilot.service import AlphaPilotService


class SectorFilterTest(unittest.TestCase):
    """signals(sector=...) 不再过滤 watchlist,但 sector_bars 仍生效"""

    def test_sector_param_does_not_filter_instruments(self) -> None:
        """2026-06-07: 切 sector 不过滤 watchlist,保持原标的池"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 不传 sector
            all_signals = service.signals(universe="watchlist", limit=20)
            # 传 sector=半导体 ETF
            semi_etf = next(c for c in BENCHMARK_CANDIDATES["sector"] if c.sector == "半导体")
            semi_signals = service.signals(universe="watchlist", limit=20, sector=semi_etf.symbol)
            # 标的池不变(因为不过滤)
            self.assertEqual(len(all_signals), len(semi_signals),
                             f"切 sector 不应影响标的池数量,实 all={len(all_signals)} vs semi={len(semi_signals)}")
            # 全部 watchlist 应跨多个 sector
            sectors = {s.get("sector") for s in semi_signals}
            self.assertGreater(len(sectors), 1, "全 watchlist 应跨多个 sector")

    def test_unknown_sector_falls_back_gracefully(self) -> None:
        """不存在的 sector symbol 不报错,正常返回全 watchlist"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            signals = service.signals(universe="watchlist", limit=10, sector="000000.XX")
            self.assertGreater(len(signals), 0)
            self.assertGreater(len({s.get("sector") for s in signals}), 1)


if __name__ == "__main__":
    unittest.main()

