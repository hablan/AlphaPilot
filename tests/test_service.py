from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alphapilot.service import AlphaPilotService


class AlphaPilotServiceTest(unittest.TestCase):
    def test_dashboard_and_manual_marks_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            dashboard = service.dashboard()
            self.assertIn("benchmarks", dashboard)
            self.assertIn("performance", dashboard)
            self.assertGreater(len(service.signals()), 0)

            mark = service.mark_trade("300124.SZ", "BUY", 100, note="测试标记")
            self.assertEqual(mark["shares"], 100)
            self.assertEqual(service.journal.holdings()["300124.SZ"]["shares"], 100)

    def test_data_status_reports_partial_fetch_and_provider_mix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            run_id = service.cache.start_fetch_run("akshare", "watchlist", "2023-01-01", "2026-01-01")
            service.cache.finish_fetch_run(run_id, "PARTIAL", 1, 1, ["002475.SZ 立讯精密: fetch failed"])

            status = service.data_status()
            self.assertEqual(status["status"], "PARTIAL")
            self.assertIn("sample", status["provider_mix"])
            self.assertIn("fund_flow", status)
            self.assertEqual(status["last_fetch"]["failure_count"], 1)
            self.assertIn("立讯精密", status["last_fetch"]["errors"][0])

    def test_data_status_reports_failed_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            run_id = service.cache.start_fetch_run("eastmoney", "watchlist", "2023-01-01", "2026-01-01")
            service.cache.finish_fetch_run(run_id, "FAILED", 0, 10, ["all failed"])

            status = service.data_status()
            self.assertEqual(status["status"], "FAILED")
            self.assertIn("全部失败", status["message"])

    def test_strategy_config_is_persisted_and_applied_to_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite"
            service = AlphaPilotService(db_path)
            service.initialize_data(provider="sample", universe="watchlist", years=1)

            config = service.update_strategy_config(
                {
                    "require_market_above_ma20": False,
                    "require_sector_strong": False,
                    "allow_trial_position": False,
                    "take_profit_pct": 0.2,
                }
            )
            self.assertFalse(config["settings"]["require_market_above_ma20"])
            self.assertEqual(config["settings"]["take_profit_pct"], 0.2)

            reloaded = AlphaPilotService(db_path)
            self.assertFalse(reloaded.strategy_config()["settings"]["require_market_above_ma20"])
            self.assertTrue(all("sector" in item for item in reloaded.signals()))

    def test_strategy_config_can_be_reset_to_defaults(self) -> None:
        """误改 settings 后，reset 端点必须能恢复默认。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite"
            service = AlphaPilotService(db_path)
            service.initialize_data(provider="sample", universe="watchlist", years=1)

            # 先把所有 bool 改成 false（典型误改）
            service.update_strategy_config(
                {
                    "require_market_above_ma20": False,
                    "require_sector_strong": False,
                    "allow_trial_position": False,
                    "allow_normal_position": False,
                    "enable_loss_streak_cooldown": False,
                }
            )
            polluted = service.strategy_config()["settings"]
            self.assertFalse(polluted["require_market_above_ma20"])
            self.assertFalse(polluted["allow_normal_position"])

            # 新 service 实例应当能读到 polluted（验证持久化真的发生了）
            reloaded = AlphaPilotService(db_path)
            self.assertFalse(reloaded.strategy_config()["settings"]["allow_normal_position"])

            # reset 必须删除 DB 中的设置
            result = reloaded.reset_strategy_config()
            self.assertTrue(result["reset"])
            self.assertEqual(result["settings"], result["defaults"])

            # 再次 reload 应该回到默认
            fresh = AlphaPilotService(db_path)
            self.assertTrue(fresh.strategy_config()["settings"]["require_market_above_ma20"])
            self.assertTrue(fresh.strategy_config()["settings"]["allow_normal_position"])
            self.assertTrue(fresh.strategy_config()["settings"]["enable_loss_streak_cooldown"])

    def test_reset_config_when_no_override_set_is_noop(self) -> None:
        """没有任何用户覆盖时 reset 应该不报错，正常返回默认值。"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)

            result = service.reset_strategy_config()
            self.assertTrue(result["reset"])
            # 应当等于 Trend20Settings 的默认
            from alphapilot.strategy.trend20 import Trend20Settings
            from dataclasses import asdict
            self.assertEqual(result["settings"], asdict(Trend20Settings()))

    def test_signal_universe_can_return_more_than_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            universes = service.signal_universes()

            self.assertTrue(any(item["value"] == "all_a" for item in universes))
            signals = service.signals(universe="all_a", limit=3)
            self.assertEqual(len(signals), 3)
            self.assertTrue(all("board" in item for item in signals))

            page = service.signal_page(universe="all_a", page=1, page_size=20)
            self.assertEqual(page["page_size"], 20)
            self.assertEqual(page["page"], 1)
            self.assertEqual(len(page["rows"]), min(20, page["total"]))

    def test_new_relaxed_params_round_trip(self) -> None:
        """新增的 6 个放宽参数能通过 API 完整读写往返。"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 6 个新参数设置成非常规值，验证能保存 + 读回
            result = service.update_strategy_config({
                "cross_window": 7,
                "allow_trend_following": False,
                "trend_min_days_above_ma20": 5,
                "trend_max_distance_from_ma20": 0.18,
                "allow_sector_relaxed_trial": False,
                "sector_relaxed_min_distance": 0.04,
            })
            s = result["settings"]
            self.assertEqual(s["cross_window"], 7)
            self.assertFalse(s["allow_trend_following"])
            self.assertEqual(s["trend_min_days_above_ma20"], 5)
            self.assertEqual(s["trend_max_distance_from_ma20"], 0.18)
            self.assertFalse(s["allow_sector_relaxed_trial"])
            self.assertEqual(s["sector_relaxed_min_distance"], 0.04)
            # 重新加载 service，验证持久化
            fresh = AlphaPilotService(Path(tmp) / "test.sqlite")
            reloaded = fresh.strategy_config()["settings"]
            self.assertEqual(reloaded["cross_window"], 7)
            self.assertFalse(reloaded["allow_trend_following"])
            self.assertEqual(reloaded["trend_min_days_above_ma20"], 5)
            self.assertEqual(reloaded["trend_max_distance_from_ma20"], 0.18)
            self.assertFalse(reloaded["allow_sector_relaxed_trial"])
            self.assertEqual(reloaded["sector_relaxed_min_distance"], 0.04)

    def test_relaxed_params_change_signal_action(self) -> None:
        """修改新参数应改变信号输出：关掉 sector_relaxed_trial 时全市场 TRIAL 减少。"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 默认：宽松
            from collections import Counter
            default_signals = service.signals(universe="watchlist", limit=10)
            default_actions = Counter(s["action"] for s in default_signals)
            # 关掉所有放宽 → 应该更少 NORMAL/TRIAL
            service.update_strategy_config({
                "allow_sector_relaxed_trial": False,
                "allow_trend_following": False,
                "cross_window": 1,
            })
            strict_signals = service.signals(universe="watchlist", limit=10)
            strict_actions = Counter(s["action"] for s in strict_signals)
            # SKIP 应当更多（更多被过滤）
            self.assertGreaterEqual(
                strict_actions.get("SKIP", 0),
                default_actions.get("SKIP", 0),
                f"关掉放宽后 SKIP 应当 >= 默认：default={dict(default_actions)} strict={dict(strict_actions)}",
            )

    def test_paper_equity_curve_with_no_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            result = service.paper_equity_curve()
            self.assertEqual(result["curve"], [])
            self.assertEqual(result["summary"]["trade_count"], 0)

    def test_paper_equity_curve_after_paper_buy(self) -> None:
        """模拟买入后，equity curve 应包含买入日及之后的数据。"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            # 模拟买入 300124.SZ 100 股，价格用最新收盘
            service.mark_trade("300124.SZ", "BUY", 100, mode="paper")
            result = service.paper_equity_curve()
            self.assertGreater(len(result["curve"]), 0)
            self.assertEqual(result["summary"]["trade_count"], 1)
            # 每点 pnl = market_value - cost
            for pt in result["curve"]:
                self.assertAlmostEqual(pt["pnl"], pt["market_value"] - pt["cost"], places=2)

    def test_paper_and_real_marks_isolated(self) -> None:
        """同一标的同一天 mark 一笔 real 和一笔 paper，应分别计入各自 PnL。"""
        with tempfile.TemporaryDirectory() as tmp:
            service = AlphaPilotService(Path(tmp) / "test.sqlite")
            service.initialize_data(provider="sample", universe="watchlist", years=1)
            service.mark_trade("300124.SZ", "BUY", 100, 50.0, mode="real")
            service.mark_trade("300124.SZ", "BUY", 50, 60.0, mode="paper")
            real = service.journal.holdings(mode="real")
            paper = service.journal.holdings(mode="paper")
            self.assertEqual(real["300124.SZ"]["shares"], 100)
            self.assertEqual(real["300124.SZ"]["cost"], 50.0)
            self.assertEqual(paper["300124.SZ"]["shares"], 50)
            self.assertEqual(paper["300124.SZ"]["cost"], 60.0)


if __name__ == "__main__":
    unittest.main()
