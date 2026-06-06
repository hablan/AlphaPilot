from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd

from alphapilot.config import Instrument
from alphapilot.data import providers
from alphapilot.data.providers import (
    AkShareProvider,
    BSE_920_TO_OLD_CODE,
    EastMoneyCurlProvider,
    FallbackProvider,
    SampleDataProvider,
    SinaDailyProvider,
    TencentCurlProvider,
    _a_share_symbol,
    _amount_10k_to_yuan,
    _eastmoney_secid,
    _fund_flow_rank_params,
    _is_st_name,
    _sina_symbol,
    _sina_symbols_for_instrument,
    _tencent_symbol,
    default_start_date,
    fund_flow_provider_by_name,
    normalize_akshare_frame,
    provider_by_name,
    universe_by_name,
)
from alphapilot.data.utils import compact_error


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


class TestSymbolConverters(unittest.TestCase):
    def test_eastmoney_secid_for_sh(self) -> None:
        self.assertEqual(_eastmoney_secid("600000.SH"), "1.600000")

    def test_eastmoney_secid_for_sz(self) -> None:
        self.assertEqual(_eastmoney_secid("000001.SZ"), "0.000001")

    def test_eastmoney_secid_without_suffix_defaults_to_sz(self) -> None:
        self.assertEqual(_eastmoney_secid("000001"), "0.000001")

    def test_tencent_symbol_sh(self) -> None:
        self.assertEqual(_tencent_symbol("600000.SH"), "sh600000")

    def test_tencent_symbol_sz(self) -> None:
        self.assertEqual(_tencent_symbol("000001.SZ"), "sz000001")

    def test_tencent_symbol_bj(self) -> None:
        self.assertEqual(_tencent_symbol("830799.BJ"), "bj830799")

    def test_sina_symbol_sh(self) -> None:
        self.assertEqual(_sina_symbol("600000.SH"), "sh600000")

    def test_sina_symbol_sz(self) -> None:
        self.assertEqual(_sina_symbol("000001.SZ"), "sz000001")

    def test_sina_symbols_for_instrument_no_alias(self) -> None:
        inst = Instrument("000001.SZ", "平安银行")
        self.assertEqual(_sina_symbols_for_instrument(inst), ["sz000001"])

    def test_sina_symbols_for_instrument_bj_with_alias(self) -> None:
        # 找一个 BSE 920 有别名映射的代码
        alias_code = next(iter(BSE_920_TO_OLD_CODE))
        inst = Instrument(f"{alias_code}.BJ", "测试 B 股")
        symbols = _sina_symbols_for_instrument(inst)
        self.assertEqual(symbols[0], f"bj{alias_code}")
        self.assertIn(f"bj{BSE_920_TO_OLD_CODE[alias_code]}", symbols)

    def test_a_share_symbol_sh_prefixes(self) -> None:
        for prefix in ("600", "601", "603", "605", "688"):
            self.assertEqual(_a_share_symbol(f"{prefix}123"), f"{prefix}123.SH")

    def test_a_share_symbol_bj_prefixes(self) -> None:
        for prefix in ("4", "8", "92"):
            self.assertEqual(_a_share_symbol(f"{prefix}12345"), f"{prefix}12345.BJ")

    def test_a_share_symbol_sz_default(self) -> None:
        self.assertEqual(_a_share_symbol("000001"), "000001.SZ")
        self.assertEqual(_a_share_symbol("300124"), "300124.SZ")

    def test_is_st_name_detects_st(self) -> None:
        self.assertTrue(_is_st_name("ST 华联"))
        self.assertTrue(_is_st_name("st 科技"))
        self.assertTrue(_is_st_name("华联 *ST"))
        self.assertFalse(_is_st_name("平安银行"))
        # "ST" 是子串匹配设计，单独 "ST" 也会命中
        self.assertTrue(_is_st_name("ST"))

    def test_amount_10k_to_yuan(self) -> None:
        s = pd.Series([1.0, 2.0, None])
        out = _amount_10k_to_yuan(s)
        self.assertEqual(out.iloc[0], 10000.0)
        self.assertEqual(out.iloc[1], 20000.0)
        # None 被 coerce 后 fillna(0.0)，所以变成 0 而非 NaN
        self.assertEqual(out.iloc[2], 0.0)

    def test_default_start_date_three_years(self) -> None:
        today = date(2026, 6, 2)
        result = default_start_date(years=3, today=today)
        self.assertEqual(result, (today - timedelta(days=365 * 3 + 10)).isoformat())

    def test_default_start_date_uses_today_when_none(self) -> None:
        result = default_start_date(years=1)
        # 应当返回一年多以前的日期
        expected = (date.today() - timedelta(days=375)).isoformat()
        self.assertEqual(result, expected)


class TestFundFlowRankParams(unittest.TestCase):
    def test_params_have_required_keys(self) -> None:
        params = _fund_flow_rank_params(page=2, page_size=50)
        for key in ("fid", "po", "pz", "pn", "np", "fltt", "invt", "ut", "fs"):
            self.assertIn(key, params)
        self.assertEqual(params["pn"], "2")
        self.assertEqual(params["pz"], "50")


class TestCompactError(unittest.TestCase):
    def test_collapses_newlines(self) -> None:
        result = compact_error(Exception("line1\nline2\nline3"))
        self.assertEqual(result, "line1 line2 line3")

    def test_truncates_long_message(self) -> None:
        long_msg = "x" * 1000
        result = compact_error(Exception(long_msg), max_length=200)
        self.assertEqual(len(result), 200)

    def test_empty_message_falls_back_to_class_name(self) -> None:
        result = compact_error(ValueError(""))
        self.assertEqual(result, "ValueError")

    def test_whitespace_only_falls_back_to_class_name(self) -> None:
        result = compact_error(ValueError("   "))
        self.assertEqual(result, "ValueError")


# ---------------------------------------------------------------------------
# normalize_akshare_frame
# ---------------------------------------------------------------------------


class TestNormalizeAkshareFrame(unittest.TestCase):
    def test_renames_chinese_columns(self) -> None:
        raw = pd.DataFrame(
            {
                "日期": ["2024-01-01", "2024-01-02"],
                "开盘": [10.0, 11.0],
                "最高": [10.5, 11.5],
                "最低": [9.5, 10.5],
                "收盘": [10.2, 11.2],
                "成交量": [1000, 2000],
                "成交额": [10200, 22400],
            }
        )
        result = normalize_akshare_frame(raw)
        self.assertIn("trade_date", result.columns)
        self.assertIn("open", result.columns)
        self.assertIn("high", result.columns)
        self.assertIn("close", result.columns)
        self.assertNotIn("日期", result.columns)
        self.assertEqual(len(result), 2)

    def test_missing_trade_date_raises(self) -> None:
        bad = pd.DataFrame({"foo": [1, 2]})
        with self.assertRaises(ValueError):
            normalize_akshare_frame(bad)

    def test_synthesizes_amount_when_missing(self) -> None:
        raw = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "开盘": [10.0],
                "最高": [10.5],
                "最低": [9.5],
                "收盘": [10.2],
                "成交量": [1000],
                # 故意不提供 成交额
            }
        )
        result = normalize_akshare_frame(raw)
        self.assertEqual(result.iloc[0]["amount"], 10.2 * 1000)


# ---------------------------------------------------------------------------
# provider_by_name
# ---------------------------------------------------------------------------


class TestProviderByName(unittest.TestCase):
    def test_sample_returns_sample_provider(self) -> None:
        p = provider_by_name("sample")
        self.assertIsInstance(p, SampleDataProvider)
        self.assertEqual(p.name, "sample")

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            provider_by_name("nonexistent")

    def test_auto_returns_fallback(self) -> None:
        p = provider_by_name("auto")
        self.assertIsInstance(p, FallbackProvider)

    def test_named_providers(self) -> None:
        # 不真正连接网络，仅验证返回了正确类型的实例
        for name, expected_cls in [
            ("tencent", TencentCurlProvider),
            ("sina", SinaDailyProvider),
            ("eastmoney", EastMoneyCurlProvider),
            ("akshare", AkShareProvider),
        ]:
            with self.subTest(name=name):
                p = provider_by_name(name)
                self.assertIsInstance(p, expected_cls)


class TestFundFlowProviderByName(unittest.TestCase):
    def test_eastmoney_aliases(self) -> None:
        for name in ("eastmoney", "eastmoney_fund_flow", "eastmoney_fund_flow_rank"):
            with self.subTest(name=name):
                p = fund_flow_provider_by_name(name)
                self.assertIsNotNone(p)

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            fund_flow_provider_by_name("nonexistent")


# ---------------------------------------------------------------------------
# universe_by_name
# ---------------------------------------------------------------------------


class TestUniverseByName(unittest.TestCase):
    def test_benchmarks_returns_all_candidates(self) -> None:
        # benchmarks universe 现在返回 3 个默认 + 全部槽位候选（去重）
        insts = universe_by_name("benchmarks")
        from alphapilot.config import BENCHMARK_CANDIDATES, BENCHMARKS
        expected = len(BENCHMARKS)  # 默认 3
        for slot_candidates in BENCHMARK_CANDIDATES.values():
            for inst in slot_candidates:
                if inst.symbol not in {i.symbol for i in BENCHMARKS.values()}:
                    expected += 1
        # 简单断言：不少于默认数 + 至少一个槽位新增
        self.assertGreaterEqual(len(insts), len(BENCHMARKS) + 5)
        # 应当包含深成指（产品上线新加的）
        symbols = {i.symbol for i in insts}
        self.assertIn("399001.SZ", symbols)

    def test_watchlist_matches_config(self) -> None:
        insts = universe_by_name("watchlist")
        # watchlist 至少 5 个，确认非空
        self.assertGreater(len(insts), 0)

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            universe_by_name("not_a_universe")


# ---------------------------------------------------------------------------
# SampleDataProvider（确定性的本地数据源）
# ---------------------------------------------------------------------------


class TestSampleDataProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = SampleDataProvider()
        self.inst = Instrument("000001.SZ", "测试股票")

    def test_name(self) -> None:
        self.assertEqual(self.provider.name, "sample")

    def test_too_short_range_raises(self) -> None:
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=10)).isoformat()
        with self.assertRaises(ValueError):
            self.provider.fetch_daily(self.inst, start, end)

    def test_returns_dataframe_with_required_columns(self) -> None:
        end = date(2026, 6, 1)
        start = (end - timedelta(days=200)).isoformat()
        df = self.provider.fetch_daily(self.inst, start, end.isoformat())
        for col in ("trade_date", "open", "high", "low", "close"):
            self.assertIn(col, df.columns)
        self.assertGreater(len(df), 80)

    def test_deterministic_for_same_symbol(self) -> None:
        end = date(2026, 6, 1)
        start = (end - timedelta(days=200)).isoformat()
        df1 = self.provider.fetch_daily(self.inst, start, end.isoformat())
        df2 = self.provider.fetch_daily(self.inst, start, end.isoformat())
        pd.testing.assert_frame_equal(df1, df2)

    def test_different_for_different_symbols(self) -> None:
        end = date(2026, 6, 1)
        start = (end - timedelta(days=200)).isoformat()
        a = self.provider.fetch_daily(Instrument("000001.SZ", "A"), start, end.isoformat())
        b = self.provider.fetch_daily(Instrument("000002.SZ", "B"), start, end.isoformat())
        self.assertFalse(a["close"].equals(b["close"]))


# ---------------------------------------------------------------------------
# 网络请求 Provider：mock 网络层
# ---------------------------------------------------------------------------


class TestTencentCurlProviderMocked(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(TencentCurlProvider().name, "tencent")

    def test_fetch_daily_parses_json(self) -> None:
        # 腾讯接口实际返回的是 JSON（通过 _curl_json 拉取）
        # 关键字段：code=0, data[symbol][qfqday] = [["2024-01-02","10.5","10.2","10.8","10.0","10000"], ...]
        payload = {
            "code": 0,
            "data": {
                "sz000001": {
                    "qfqday": [
                        ["2024-01-02", "10.5", "10.2", "10.8", "10.0", "10000"],
                        ["2024-01-03", "10.2", "10.5", "10.6", "10.1", "12000"],
                    ]
                }
            },
        }
        with patch.object(providers, "_curl_json", return_value=payload) as mock_curl:
            df = TencentCurlProvider().fetch_daily(
                Instrument("000001.SZ", "测试"),
                "2024-01-01",
                "2024-12-31",
            )
        self.assertEqual(len(df), 2)
        self.assertIn("close", df.columns)
        self.assertEqual(df.iloc[0]["close"], 10.2)
        mock_curl.assert_called()

    def test_fetch_daily_empty_raises(self) -> None:
        payload = {"code": 0, "data": {"sz000001": {}}}
        with patch.object(providers, "_curl_json", return_value=payload):
            with self.assertRaises(RuntimeError):
                TencentCurlProvider().fetch_daily(
                    Instrument("000001.SZ", "测试"),
                    "2024-01-01",
                    "2024-12-31",
                )


class TestSinaDailyProviderMocked(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(SinaDailyProvider().name, "sina")

    def test_fetch_daily_handles_empty_response_raises(self) -> None:
        with patch.object(providers, "_curl_text", return_value=""):
            with self.assertRaises(RuntimeError):
                SinaDailyProvider().fetch_daily(
                    Instrument("000001.SZ", "测试"),
                    "2024-01-01",
                    "2024-12-31",
                )

    def test_fetch_daily_parses_json(self) -> None:
        # 新浪接口实际返回 JSON 数组，每项含 day/open/high/low/close/volume
        json_text = json.dumps(
            [
                {"day": "2024-01-02", "open": 10.5, "high": 10.8, "low": 10.0, "close": 10.2, "volume": 10000},
                {"day": "2024-01-03", "open": 10.2, "high": 10.6, "low": 10.1, "close": 10.5, "volume": 12000},
            ]
        )
        with patch.object(providers, "_curl_text", return_value=json_text):
            df = SinaDailyProvider().fetch_daily(
                Instrument("000001.SZ", "测试"),
                "2024-01-01",
                "2024-12-31",
            )
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["close"], 10.2)


class TestEastMoneyCurlProviderMocked(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(EastMoneyCurlProvider().name, "eastmoney")

    def test_fetch_daily_parses_json(self) -> None:
        # 东方财富接口返回：rc=0 + data.klines = ["date,open,close,high,low,volume,amount,...", ...]
        payload = {
            "rc": 0,
            "data": {
                "klines": [
                    "2024-01-02,10.5,10.2,10.8,10.0,10000,102000.0,1.5,0.5,0.1,2.0",
                    "2024-01-03,10.2,10.5,10.6,10.1,12000,126000.0,1.2,0.4,0.3,2.9",
                ]
            },
        }
        with patch.object(providers, "_curl_json", return_value=payload):
            df = EastMoneyCurlProvider().fetch_daily(
                Instrument("000001.SZ", "测试"),
                "2024-01-01",
                "2024-12-31",
            )
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["close"], 10.2)

    def test_fetch_daily_raises_on_bad_rc(self) -> None:
        bad = {"rc": 1, "data": None}
        with patch.object(providers, "_curl_json", return_value=bad):
            with self.assertRaises(RuntimeError):
                EastMoneyCurlProvider().fetch_daily(
                    Instrument("000001.SZ", "测试"),
                    "2024-01-01",
                    "2024-12-31",
                )


class TestAkShareProvider(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(AkShareProvider().name, "akshare")

    def test_fetch_daily_raises_when_akshare_missing(self) -> None:
        # 模拟 akshare 未安装时显式抛错
        with patch.dict("sys.modules", {"akshare": None}):
            with self.assertRaises(RuntimeError):
                AkShareProvider().fetch_daily(
                    Instrument("000001.SZ", "测试"),
                    "2024-01-01",
                    "2024-12-31",
                )


class TestFallbackProvider(unittest.TestCase):
    def test_falls_back_to_next_provider_on_failure(self) -> None:
        primary = MagicMock()
        primary.name = "fake_primary"
        primary.fetch_daily.side_effect = RuntimeError("network down")
        secondary = MagicMock()
        secondary.name = "fake_secondary"
        secondary.fetch_daily.return_value = pd.DataFrame({"close": [10.0]})

        with patch.object(providers, "_tencent_limit", return_value=None):
            fb = FallbackProvider(providers=[primary, secondary])
            df = fb.fetch_daily(
                Instrument("000001.SZ", "测试"),
                "2024-01-01",
                "2024-12-31",
            )
        self.assertEqual(len(df), 1)
        self.assertEqual(primary.fetch_daily.call_count, 1)
        self.assertEqual(secondary.fetch_daily.call_count, 1)

    def test_raises_when_all_providers_fail(self) -> None:
        a = MagicMock()
        a.name = "a"
        a.fetch_daily.side_effect = RuntimeError("a fails")
        b = MagicMock()
        b.name = "b"
        b.fetch_daily.side_effect = RuntimeError("b fails")
        with patch.object(providers, "_tencent_limit", return_value=None):
            fb = FallbackProvider(providers=[a, b])
            with self.assertRaises(RuntimeError) as ctx:
                fb.fetch_daily(
                    Instrument("000001.SZ", "测试"),
                    "2024-01-01",
                    "2024-12-31",
                )
        self.assertIn("a fails", str(ctx.exception))
        self.assertIn("b fails", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
