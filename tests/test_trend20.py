from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from alphapilot.config import BENCHMARKS, WATCHLIST
from alphapilot.data.bootstrap import initialize_market_cache
from alphapilot.data.cache import MarketDataCache
from alphapilot.strategy.trend20 import Trend20Engine, Trend20Settings


class Trend20EngineTest(unittest.TestCase):
    def test_loss_streak_stops_new_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            initialize_market_cache(cache, provider_name="sample", universe_name="watchlist", years=1)
            engine = Trend20Engine()
            stock = WATCHLIST[0]
            signal = engine.evaluate(
                code=stock.symbol,
                name=stock.name,
                bars=cache.get_bars(stock.symbol),
                market_bars=cache.get_bars(BENCHMARKS["market"].symbol),
                sector_bars=cache.get_bars(BENCHMARKS["sector"].symbol),
                leader_bars=cache.get_bars("601138.SH"),
                loss_streak=3,
            )

            self.assertEqual(signal.action, "STOP")
            self.assertIn("连续亏损", signal.blocked_reasons[0])

    def test_signal_contains_estimated_win_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = MarketDataCache(Path(tmp) / "test.sqlite")
            initialize_market_cache(cache, provider_name="sample", universe_name="watchlist", years=1)
            engine = Trend20Engine()
            stock = WATCHLIST[0]
            signal = engine.evaluate(
                code=stock.symbol,
                name=stock.name,
                bars=cache.get_bars(stock.symbol),
                market_bars=cache.get_bars(BENCHMARKS["market"].symbol),
                sector_bars=cache.get_bars(BENCHMARKS["sector"].symbol),
                leader_bars=cache.get_bars("601138.SH"),
            )

            self.assertGreaterEqual(signal.estimated_win_rate, 0.2)
            self.assertLessEqual(signal.estimated_win_rate, 0.76)
            self.assertIn(signal.action, {"NORMAL", "TRIAL", "SKIP", "EXIT_ALERT", "STOP"})

    def test_disabled_market_and_sector_filters_do_not_block_entry(self) -> None:
        stock = _bars([10.0] * 68 + [9.8, 10.2])
        market = _bars([10.0] * 50 + [9.9 - index * 0.02 for index in range(20)])
        sector = _bars([10.0] * 50 + [9.8 - index * 0.02 for index in range(20)])
        leader = _bars([10.0] * 50 + [10.0 + index * 0.04 for index in range(20)])

        # 显式开启 sector 过滤来测"被卡"的路径
        blocked = Trend20Engine(
            Trend20Settings(require_market_above_ma20=False, require_sector_strong=True)
        ).evaluate("000001.SZ", "测试", stock, market, sector, leader)
        relaxed = Trend20Engine(
            Trend20Settings(require_market_above_ma20=False, require_sector_strong=False)
        ).evaluate("000001.SZ", "测试", stock, market, sector, leader)

        self.assertEqual(blocked.action, "SKIP")
        self.assertIn("板块未共振", blocked.blocked_reasons[0])
        self.assertIn(relaxed.action, {"NORMAL", "TRIAL"})
        self.assertNotIn("大盘在 MA20 下方", "；".join(relaxed.blocked_reasons))

    # --- 数据长度边界 ----------------------------------------------------

    def test_insufficient_data_returns_skip(self) -> None:
        # 64 个交易日（< 65）应直接返回 SKIP
        stock = _bars([10.0] * 64)
        market = _bars([10.0] * 64)
        sector = _bars([10.0] * 64)
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector,
        )
        self.assertEqual(signal.action, "SKIP")
        self.assertEqual(signal.signal_type, "RISK")
        self.assertIn("数据不足", "".join(signal.blocked_reasons))

    def test_exactly_65_bars_runs_through_evaluation(self) -> None:
        # 65 个交易日正好满足最小要求
        stock = _bars([10.0] * 64 + [10.5])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        # 不应是 SKIP / 数据不足
        self.assertNotIn("数据不足", "".join(signal.blocked_reasons))

    def test_66_bars_evaluates_normally(self) -> None:
        # 66 个交易日
        stock = _bars([10.0] * 65 + [10.5])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(16)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(16)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(16)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        self.assertIsNotNone(signal.signal_date)

    # --- estimate_win_rate 边界 -----------------------------------------

    def test_win_rate_bounds(self) -> None:
        from alphapilot.strategy.trend20 import estimate_win_rate
        # 最低情况：所有 gate 关闭、SKIP、连亏
        rate = estimate_win_rate(
            gates={},
            action="SKIP",
            loss_streak=10,
        )
        # 0.42 - 0.08 (SKIP) - 0.04*3 (min(loss_streak, 3)) = 0.42 - 0.08 - 0.12 = 0.22
        # 但 max 下限是 0.20
        self.assertGreaterEqual(rate, 0.20)
        self.assertLessEqual(rate, 0.76)

    def test_win_rate_ceiling(self) -> None:
        from alphapilot.strategy.trend20 import estimate_win_rate
        rate = estimate_win_rate(
            gates={
                "market_above_ma20": True,
                "sector_strong": True,
                "leader_strong": True,
                "just_crossed_ma20": True,
                "low_position": True,
            },
            action="NORMAL",
            loss_streak=0,
        )
        # 上限 0.76
        self.assertLessEqual(rate, 0.76)
        # 0.42 + 0.06 + 0.08 + 0.05 + 0.03 + 0.04 + 0.03 = 0.71
        self.assertEqual(rate, 0.71)

    def test_win_rate_floor(self) -> None:
        from alphapilot.strategy.trend20 import estimate_win_rate
        rate = estimate_win_rate(
            gates={},
            action="STOP",
            loss_streak=10,
        )
        # 0.42 - 0.08 (STOP) - 0.04*3 = 0.22
        # 但 0.22 仍然 ≥ 0.20
        self.assertEqual(rate, round(0.22, 4))

    # --- Trend20Settings 参数夹逼 --------------------------------------

    def test_settings_clamps_out_of_range_values(self) -> None:
        s = Trend20Settings(cooldown_loss_count=999, cooldown_days=-5)
        self.assertEqual(s.cooldown_loss_count, 10)
        self.assertEqual(s.cooldown_days, 1)

    def test_settings_clamps_positive_take_profit(self) -> None:
        s = Trend20Settings(take_profit_pct=-0.5)
        self.assertEqual(s.take_profit_pct, 0.15)  # fallback

    def test_settings_clamps_positive_stop_loss(self) -> None:
        s = Trend20Settings(stop_loss_pct=0.5)
        self.assertEqual(s.stop_loss_pct, -0.10)  # fallback

    def test_settings_passes_through_valid_values(self) -> None:
        s = Trend20Settings(cooldown_loss_count=5, cooldown_days=20, take_profit_pct=0.2)
        self.assertEqual(s.cooldown_loss_count, 5)
        self.assertEqual(s.cooldown_days, 20)
        self.assertEqual(s.take_profit_pct, 0.2)

    # --- add_indicators 行为 -------------------------------------------

    def test_add_indicators_computes_all_columns(self) -> None:
        from alphapilot.strategy.trend20 import add_indicators
        df = _bars([10.0 + i * 0.1 for i in range(80)])
        result = add_indicators(df)
        for col in ("ma20", "ma20_slope", "ret20", "ret15", "high60", "distance_ma20", "drawdown_from_60h"):
            self.assertIn(col, result.columns)
        # 前 19 行 ma20 应为 NaN
        self.assertTrue(pd.isna(result["ma20"].iloc[0]))
        # 第 20 行起有值
        self.assertFalse(pd.isna(result["ma20"].iloc[19]))

    # --- 持仓 + 盈亏计算 -----------------------------------------------

    def test_holding_with_profit_yields_positive_pnl(self) -> None:
        stock = _bars([10.0] * 64 + [10.5])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ",
            "测试",
            stock,
            market,
            sector,
            leader,
            holding_shares=1000,
            cost_price=10.0,
        )
        # 10.5/10.0 - 1 = 0.05
        self.assertIsNotNone(signal.pnl_pct)
        self.assertAlmostEqual(signal.pnl_pct, 0.05, places=4)

    def test_holding_with_loss_triggers_exit_alert(self) -> None:
        # 构造明显跌破 MA20 的持仓
        # 前 64 个稳定 10.0，最后 5 个急跌到 8.5
        stock = _bars([10.0] * 60 + [10.0, 9.5, 9.0, 8.5, 8.5, 8.5])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ",
            "测试",
            stock,
            market,
            sector,
            leader,
            holding_shares=1000,
            cost_price=10.0,
        )
        # 跌破 MA20 应该产生 EXIT_ALERT 或 blocked
        self.assertIn(signal.action, {"EXIT_ALERT", "SKIP"})

    # --- 新放宽入场条件（cross_window / trend_following / sector_relaxed） ---

    def test_trend_confirmed_gives_trial_in_steady_uptrend(self) -> None:
        """震荡上行：个股站上 MA20 持续 3 天以上，应出 TRIAL（趋势确认入场）。"""
        # 价格 10.0 持续 65 天，最后 5 天涨到 11.0（站上 MA20 距离约 10%）
        stock = _bars([10.0] * 65 + [10.4, 10.6, 10.8, 11.0, 11.0])
        # 大盘平稳
        market = _bars([10.0] * 50 + [10.0 + i * 0.005 for i in range(15)])
        # 板块弱（最后一个值在 MA20 下方）
        sector = _bars([10.0] * 50 + [10.0 - i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        # 趋势确认 + 板块放宽 → TRIAL
        self.assertIn(signal.action, {"NORMAL", "TRIAL"})
        self.assertTrue(signal.gate_state.get("trend_confirmed"))

    def test_cross_window_relaxes_just_crossed(self) -> None:
        """金叉窗口：过去 3 天内金叉都算"刚金叉"（不只单日金叉）。"""
        # 价格 9.8 持续 65 天（远在 MA20 下方），最后 1 天涨到 10.5（刚金叉）
        stock = _bars([9.8] * 65 + [10.5])
        market = _bars([10.0] * 50 + [10.0 + i * 0.005 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 - i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        # 默认 cross_window=3，单日金叉应被窗口捕获
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        self.assertTrue(signal.gate_state.get("just_crossed_ma20"))

    def test_sector_relaxed_trial_when_sector_weak(self) -> None:
        """板块弱但个股强势（站上 MA20 + 距离 ≥ 2%），应出 TRIAL。

        显式开启 require_sector_strong 才能走 sector_relaxed_trial 路径。
        """
        # 个股稳步上涨，站上 MA20 持续运行
        stock = _bars([10.0] * 65 + [10.4, 10.5, 10.6, 10.7, 10.8])
        market = _bars([10.0] * 50 + [10.0 + i * 0.005 for i in range(15)])
        # 板块弱（最后一天 close 9.5 < MA20）
        sector = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        # 显式开启 require_sector_strong 走 sector_relaxed_trial 路径
        signal = Trend20Engine(Trend20Settings(require_sector_strong=True)).evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        self.assertTrue(signal.gate_state.get("sector_relaxed_trial"))
        self.assertEqual(signal.action, "TRIAL")

    def test_default_settings_open_position_in_weak_market(self) -> None:
        """默认配置（require_sector_strong=False）下，震荡市也能出 TRIAL/NORMAL。"""
        # 个股稳步上涨，站上 MA20 持续运行
        stock = _bars([10.0] * 65 + [10.4, 10.5, 10.6, 10.7, 10.8])
        market = _bars([10.0] * 50 + [10.0 + i * 0.005 for i in range(15)])
        # 板块弱（最后一天 close 9.5 < MA20）
        sector = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        # 默认 settings：sector_ok 直接为 True（require_sector_strong=False）
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
        )
        # 默认配置不再走 sector_relaxed_trial（那个路径要求 require_sector_strong=True）
        # 但仍应能给出 TRIAL（trend_confirmed 路径）
        self.assertIn(signal.action, {"NORMAL", "TRIAL"})

    # --- EXIT_ALERT 抖动过滤 ---

    def test_sector_weak_does_not_trigger_exit_alert(self) -> None:
        """sector_weak 不应再触发 EXIT_ALERT（板块弱 ≠ 退出）。"""
        # 持仓小幅盈利，仅 sector 弱
        stock = _bars([10.0] * 60 + [10.0, 10.05, 10.1, 10.1, 10.1, 10.1])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        # 板块弱
        sector = _bars([10.0] * 50 + [10.0 - i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 - i * 0.005 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
            holding_shares=1000, cost_price=10.0,
        )
        # sector_weak 不能触发 EXIT_ALERT（之前会触发，现在不应）
        self.assertNotEqual(signal.action, "EXIT_ALERT", "板块弱不应触发退出提醒")
        # 浮盈太小也不应触发 profit_alert
        if signal.action == "SKIP":
            for reason in signal.blocked_reasons:
                self.assertNotIn("盈利提醒", reason)

    def test_tiny_pnl_below_threshold_does_not_trigger(self) -> None:
        """浮盈/浮亏绝对值小于 min_exit_pnl_abs 不应触发 EXIT_ALERT。"""
        # 持仓浮盈 0.7% (default min_exit_pnl_abs=3%)
        stock = _bars([10.0] * 60 + [10.0, 10.01, 10.02, 10.05, 10.07, 10.07])
        market = _bars([10.0] * 50 + [10.0 + i * 0.005 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
            holding_shares=1000, cost_price=10.0,
        )
        # 浮盈仅 0.7%，远低于默认 min_exit_pnl_abs=3%，不应触发 profit_alert
        self.assertFalse(signal.exit_signal.get("profit_alert", False), "浮盈 0.7% 不应触发 profit_alert")

    def test_below_ma20_requires_streak(self) -> None:
        """below_ma20 必须连续 N 天跌破才触发 EXIT_ALERT（默认 3 天）。"""
        # 前 64 天稳定 10.0，最后 2 天 9.95 (仅 2 天跌破 MA20)
        stock = _bars([10.0] * 60 + [10.0, 10.0, 10.0, 10.0, 9.97, 9.95])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
            holding_shares=1000, cost_price=10.0,
        )
        # 仅 2 天跌破，不应触发 below_ma20_alert
        self.assertFalse(signal.exit_signal.get("below_ma20", False), "2 天跌破不应触发")

    def test_5_days_below_ma20_triggers(self) -> None:
        """连续 5 天跌破 MA20 应触发 EXIT_ALERT。"""
        stock = _bars([10.0] * 60 + [9.97, 9.95, 9.93, 9.92, 9.9, 9.88])
        market = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        sector = _bars([10.0] * 50 + [10.0 + i * 0.01 for i in range(15)])
        leader = _bars([10.0] * 50 + [10.0 + i * 0.015 for i in range(15)])
        signal = Trend20Engine().evaluate(
            "000001.SZ", "测试", stock, market, sector, leader,
            holding_shares=1000, cost_price=10.0,
        )
        self.assertTrue(signal.exit_signal.get("below_ma20", False), "5 天跌破应触发")
        # 由于持亏损可能同时也触发 risk_alert，只要下面有触发即可
        self.assertIn(signal.action, {"EXIT_ALERT", "SKIP"})



def _bars(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    rows = []
    for trade_date, close in zip(dates, closes):
        rows.append(
            {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000000.0,
                "amount": close * 1000000.0,
            }
        )
    return pd.DataFrame(rows)



if __name__ == "__main__":
    unittest.main()
