from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from alphapilot.config import Instrument, WATCHLIST
from alphapilot.data.bootstrap import initialize_market_cache
from alphapilot.data.cache import MarketDataCache
from alphapilot.data.providers import FallbackProvider, SampleDataProvider, TushareMoneyflowDcProvider, _a_share_symbol, _eastmoney_secid, _is_st_name, _sina_symbol, _sina_symbols_for_instrument, _tencent_symbol


class MarketDataCacheTest(unittest.TestCase):
    def test_upsert_bars_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            provider = SampleDataProvider()
            instrument = WATCHLIST[0]
            bars = provider.fetch_daily(instrument, "2024-01-01", "2024-06-30")
            first = cache.upsert_bars(instrument.symbol, bars, provider=provider.name)
            second = cache.upsert_bars(instrument.symbol, bars, provider=provider.name)
            stored = cache.get_bars(instrument.symbol)

            self.assertEqual(first, second)
            self.assertEqual(len(stored), first)
            self.assertEqual(cache.latest_trade_date(instrument.symbol), stored.iloc[-1]["trade_date"])

    def test_default_read_uses_latest_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            provider = SampleDataProvider()
            instrument = WATCHLIST[0]
            bars = provider.fetch_daily(instrument, "2024-01-01", "2024-06-30")
            cache.upsert_bars(instrument.symbol, bars, provider="old_source", data_version="old")
            newer = bars.copy()
            newer["close"] = newer["close"] + 10
            cache.upsert_bars(instrument.symbol, newer, provider="new_source", data_version="new")

            stored = cache.get_bars(instrument.symbol)
            self.assertEqual(cache.latest_provider(instrument.symbol), "new_source")
            self.assertAlmostEqual(float(stored.iloc[-1]["close"]), float(newer.iloc[-1]["close"]))

    def test_get_bars_many_returns_latest_provider_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            provider = SampleDataProvider()
            for instrument in WATCHLIST[:2]:
                bars = provider.fetch_daily(instrument, "2024-01-01", "2024-06-30")
                cache.upsert_bars(instrument.symbol, bars, provider="old_source", data_version="old")
                newer = bars.copy()
                newer["close"] = newer["close"] + 5
                cache.upsert_bars(instrument.symbol, newer, provider="new_source", data_version="new")

            frames = cache.get_bars_many([item.symbol for item in WATCHLIST[:2]])

            self.assertEqual(set(frames), {item.symbol for item in WATCHLIST[:2]})
            self.assertTrue(all(frame["provider"].eq("new_source").all() for frame in frames.values()))

            limited = cache.get_bars_many([WATCHLIST[0].symbol], limit_rows=5)
            self.assertEqual(len(limited[WATCHLIST[0].symbol]), 5)

    def test_eastmoney_secid_mapping(self) -> None:
        self.assertEqual(_eastmoney_secid("002415.SZ"), "0.002415")
        self.assertEqual(_eastmoney_secid("688256.SH"), "1.688256")

    def test_tencent_symbol_mapping(self) -> None:
        self.assertEqual(_tencent_symbol("002415.SZ"), "sz002415")
        self.assertEqual(_tencent_symbol("688256.SH"), "sh688256")
        self.assertEqual(_tencent_symbol("920218.BJ"), "bj920218")

    def test_sina_symbol_mapping(self) -> None:
        self.assertEqual(_sina_symbol("002415.SZ"), "sz002415")
        self.assertEqual(_sina_symbol("688256.SH"), "sh688256")
        self.assertEqual(_sina_symbol("920218.BJ"), "bj920218")

    def test_sina_uses_bse_920_legacy_alias(self) -> None:
        symbols = _sina_symbols_for_instrument(Instrument("920508.BJ", "殷图网联", "stock", "A股"))

        self.assertEqual(symbols, ["bj920508", "bj835508"])

    def test_a_share_symbol_and_st_filter_helpers(self) -> None:
        self.assertEqual(_a_share_symbol("688256"), "688256.SH")
        self.assertEqual(_a_share_symbol("300124"), "300124.SZ")
        self.assertEqual(_a_share_symbol("920218"), "920218.BJ")
        self.assertTrue(_is_st_name("*ST禾信"))
        self.assertTrue(_is_st_name("ST测试"))
        self.assertFalse(_is_st_name("海康威视"))

    def test_symbol_fetch_status_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            cache.record_symbol_fetch_status("002415.SZ", "海康威视", "eastmoney", "FAILED", "2026-05-29", 0, "timeout")

            statuses = cache.fetch_symbol_statuses()
            self.assertEqual(statuses[0]["symbol"], "002415.SZ")
            self.assertEqual(statuses[0]["status"], "FAILED")
            self.assertEqual(statuses[0]["message"], "timeout")

    def test_resume_skip_does_not_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            initialize_market_cache(cache, provider_name="sample", universe_name="watchlist", years=3, max_symbols=1)

            with patch("alphapilot.data.bootstrap.time.sleep") as sleep:
                result = initialize_market_cache(
                    cache,
                    provider_name="sample",
                    universe_name="watchlist",
                    years=3,
                    request_interval_seconds=10,
                    resume=True,
                    max_symbols=1,
                )

            self.assertEqual(result["skipped_count"], 1)
            sleep.assert_not_called()

    def test_upsert_fund_flows_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            frame = SampleDataProvider().fetch_daily(WATCHLIST[0], "2024-01-01", "2024-06-30").head(3)
            flows = frame[["trade_date", "close"]].copy()
            flows["pct_change"] = 0.0
            flows["main_net"] = 1.0
            flows["main_ratio"] = 0.1
            flows["super_large_net"] = 2.0
            flows["super_large_ratio"] = 0.2
            flows["large_net"] = 3.0
            flows["large_ratio"] = 0.3
            flows["medium_net"] = 4.0
            flows["medium_ratio"] = 0.4
            flows["small_net"] = 5.0
            flows["small_ratio"] = 0.5

            first = cache.upsert_fund_flows(WATCHLIST[0].symbol, flows, provider="eastmoney_fund_flow")
            second = cache.upsert_fund_flows(WATCHLIST[0].symbol, flows, provider="eastmoney_fund_flow")
            summary = cache.fund_flow_summary(WATCHLIST[0].symbol)

            self.assertEqual(first, second)
            self.assertEqual(summary["row_count"], first)
            self.assertEqual(summary["latest_trade_date"], str(flows.iloc[-1]["trade_date"]))

    def test_cache_status_tracks_tushare_fund_flow_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            run_id = cache.start_fetch_run("tushare_moneyflow_dc", "all_a", "2026-04-26", "2026-05-31")
            cache.finish_fetch_run(run_id, "FAILED", 0, 1, ["missing token"])

            status = cache.cache_status()

            self.assertEqual(status["last_fund_flow_fetch_run"]["provider"], "tushare_moneyflow_dc")
            self.assertEqual(status["last_fund_flow_fetch_run"]["status"], "FAILED")

    def test_tushare_moneyflow_normalizes_amount_unit_to_yuan(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "20260529",
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "close": 10.0,
                    "pct_change": 1.2,
                    "net_amount": 12.5,
                    "net_amount_rate": 0.8,
                    "buy_elg_amount": 2.0,
                    "buy_elg_amount_rate": 0.1,
                    "buy_lg_amount": 3.0,
                    "buy_lg_amount_rate": 0.2,
                    "buy_md_amount": 4.0,
                    "buy_md_amount_rate": 0.3,
                    "buy_sm_amount": 5.0,
                    "buy_sm_amount_rate": 0.4,
                }
            ]
        )

        with patch.dict("os.environ", {"TUSHARE_TOKEN": "token"}), patch(
            "alphapilot.data.providers._tushare_api", return_value=frame
        ):
            flows = TushareMoneyflowDcProvider(request_interval_seconds=0).fetch_all_daily("2026-05-29", "2026-05-29")

        self.assertEqual(flows.iloc[0]["symbol"], "000001.SZ")
        self.assertEqual(flows.iloc[0]["trade_date"], "2026-05-29")
        self.assertEqual(float(flows.iloc[0]["main_net"]), 125000.0)
        self.assertEqual(float(flows.iloc[0]["super_large_net"]), 20000.0)

    def test_fallback_provider_uses_next_source(self) -> None:
        class FailingProvider:
            name = "fail"

            def fetch_daily(self, instrument, start_date, end_date, adjust_type="qfq"):
                raise RuntimeError("temporary failure")

        class PassingProvider:
            name = "pass"

            def fetch_daily(self, instrument, start_date, end_date, adjust_type="qfq"):
                frame = SampleDataProvider().fetch_daily(instrument, start_date, end_date, adjust_type)
                frame.attrs["provider"] = self.name
                return frame

        provider = FallbackProvider(providers=[FailingProvider(), PassingProvider()])
        bars = provider.fetch_daily(WATCHLIST[0], "2024-01-01", "2024-06-30")
        self.assertFalse(bars.empty)
        self.assertEqual(bars.attrs["provider"], "pass")


if __name__ == "__main__":
    unittest.main()
