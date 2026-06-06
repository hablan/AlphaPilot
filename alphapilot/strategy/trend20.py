from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from alphapilot.i18n import (
    MSG_FILTERS_RELAXED,
    MSG_INSUFFICIENT_DATA,
    MSG_LEADER_STRONGER_THAN_SECTOR,
    MSG_LOSS_STREAK_COOLDOWN,
    MSG_MARKET_BELOW_MA20,
    MSG_MARKET_SECTOR_STOCK_RESONANT,
    MSG_POSITION_EXIT_TRIGGERED,
    MSG_SECTOR_NOT_RESONANT,
    MSG_STOCK_JUST_CROSSED_MA20,
    MSG_STOCK_LOW_POSITION_CROSS_MA20,
    MSG_STOCK_NOT_CROSSED_OR_RESONANT,
    MSG_STOCK_SECTOR_RELAXED_TRIAL,
    MSG_STOCK_TREND_FOLLOWING,
)
from alphapilot.models import Signal
from alphapilot.strategy.base import StrategyEngine


@dataclass
class Trend20Settings:
    # 默认值：放宽 sector / 趋势过滤，让震荡市也能出信号
    # 用户可在配置页打开 require_sector_strong 切回保守模式
    require_market_above_ma20: bool = True
    require_sector_strong: bool = False  # 默认关闭：板块弱不阻塞开仓
    allow_trial_position: bool = True
    allow_normal_position: bool = True
    enable_loss_streak_cooldown: bool = True
    cooldown_loss_count: int = 3
    cooldown_days: int = 10
    take_profit_pct: float = 0.15
    stop_loss_pct: float = -0.10
    max_distance_from_ma20: float = 0.06
    low_position_drawdown_from_60d_high: float = 0.08
    # === EXIT_ALERT 抖动过滤 ===
    # 最小盈亏阈值：盈/亏绝对值小于此值不触发 EXIT_ALERT（避免微小波动误报）
    min_exit_pnl_abs: float = 0.03
    # below_ma20 持续天数：连续跌破 MA20 多少天才算"真跌破"（避免单日假跌破）
    below_ma20_min_days: int = 3
    # === 新增：放宽入场条件，让策略在震荡市也能产出信号 ===
    # 金叉窗口：过去 N 天内任意一天金叉都算"刚金叉"（默认 3 天，从单日金叉放宽）
    cross_window: int = 3
    # 是否允许"已站上 MA20 持续 N 天"的趋势确认入场（不要求刚金叉）
    allow_trend_following: bool = True
    # 趋势确认的最小持续天数（站上 MA20 几天才算"趋势确认"）
    trend_min_days_above_ma20: int = 3
    # 趋势确认允许的最大距离（避免追已经涨很高的）
    trend_max_distance_from_ma20: float = 0.12
    # 板块弱时是否允许 TRIAL（小仓试探，不要求全共振）
    allow_sector_relaxed_trial: bool = True
    # 板块弱时 TRIAL 要求的最低距离（站上 MA20 至少多少）
    sector_relaxed_min_distance: float = 0.02

    def __post_init__(self) -> None:
        # 强制把配置夹在合理区间内，避免手抖填了越界值
        self.cooldown_loss_count = max(1, min(int(self.cooldown_loss_count), 10))
        self.cooldown_days = max(1, min(int(self.cooldown_days), 60))
        self.take_profit_pct = float(self.take_profit_pct)
        self.stop_loss_pct = float(self.stop_loss_pct)
        if self.take_profit_pct <= 0:
            self.take_profit_pct = 0.15
        if self.stop_loss_pct >= 0:
            self.stop_loss_pct = -0.10
        self.cross_window = max(1, min(int(self.cross_window), 20))
        self.trend_min_days_above_ma20 = max(1, min(int(self.trend_min_days_above_ma20), 30))
        self.trend_max_distance_from_ma20 = max(0.01, float(self.trend_max_distance_from_ma20))
        self.sector_relaxed_min_distance = max(0.0, float(self.sector_relaxed_min_distance))
        self.min_exit_pnl_abs = max(0.0, float(self.min_exit_pnl_abs))
        self.below_ma20_min_days = max(1, min(int(self.below_ma20_min_days), 20))


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy().sort_values("trade_date").reset_index(drop=True)
    frame["ma20"] = frame["close"].rolling(20).mean()
    frame["ma20_slope"] = frame["ma20"].pct_change(5, fill_method=None)
    frame["ret20"] = frame["close"].pct_change(20, fill_method=None)
    frame["ret15"] = frame["close"].pct_change(15, fill_method=None)
    frame["high60"] = frame["high"].rolling(60).max()
    frame["distance_ma20"] = frame["close"] / frame["ma20"] - 1
    frame["drawdown_from_60h"] = frame["close"] / frame["high60"] - 1
    return frame


def _last_on_or_before(bars: pd.DataFrame, as_of: Optional[str]) -> pd.DataFrame:
    if as_of is None:
        return bars
    return bars[bars["trade_date"] <= as_of]


def _bool(value: object) -> bool:
    return bool(value) and not pd.isna(value)


def estimate_win_rate(gates: dict, action: str, loss_streak: int) -> float:
    rate = 0.42
    if gates.get("market_above_ma20"):
        rate += 0.06
    if gates.get("sector_strong"):
        rate += 0.08
    if gates.get("leader_strong"):
        rate += 0.05
    if gates.get("just_crossed_ma20"):
        rate += 0.03
    if gates.get("low_position"):
        rate += 0.04
    if action == "NORMAL":
        rate += 0.03
    elif action == "TRIAL":
        rate -= 0.02
    elif action in {"SKIP", "STOP"}:
        rate -= 0.08
    rate -= min(loss_streak, 3) * 0.04
    return round(max(0.20, min(rate, 0.76)), 4)


class Trend20Engine(StrategyEngine):
    name = "trend20"

    def __init__(self, settings: Optional[Trend20Settings] = None):
        self.settings = settings or Trend20Settings()

    def evaluate(
        self,
        code: str,
        name: str,
        bars: pd.DataFrame,
        market_bars: pd.DataFrame,
        sector_bars: pd.DataFrame,
        leader_bars: Optional[pd.DataFrame] = None,
        market_indicators: Optional[pd.DataFrame] = None,
        sector_indicators: Optional[pd.DataFrame] = None,
        leader_indicators: Optional[pd.DataFrame] = None,
        as_of: Optional[str] = None,
        loss_streak: int = 0,
        holding_shares: int = 0,
        cost_price: Optional[float] = None,
    ) -> Signal:
        bars_i = add_indicators(_last_on_or_before(bars, as_of))
        market_i = market_indicators if market_indicators is not None else add_indicators(_last_on_or_before(market_bars, as_of))
        sector_i = sector_indicators if sector_indicators is not None else add_indicators(_last_on_or_before(sector_bars, as_of))
        if leader_indicators is not None:
            leader_i = leader_indicators
        else:
            leader_i = add_indicators(_last_on_or_before(leader_bars, as_of)) if leader_bars is not None else sector_i

        if min(len(bars_i), len(market_i), len(sector_i), len(leader_i)) < 65:
            latest_date = str(bars_i["trade_date"].iloc[-1]) if not bars_i.empty else as_of or ""
            return Signal(
                code=code,
                name=name,
                signal_date=latest_date,
                signal_type="RISK",
                action="SKIP",
                score=0,
                estimated_win_rate=0.0,
                blocked_reasons=[MSG_INSUFFICIENT_DATA],
                holding_shares=holding_shares,
            )

        latest = bars_i.iloc[-1]
        previous = bars_i.iloc[-2]
        market = market_i.iloc[-1]
        sector = sector_i.iloc[-1]
        leader = leader_i.iloc[-1]

        market_above = _bool(market["close"] > market["ma20"])
        sector_strong = _bool((sector["close"] > sector["ma20"]) and (sector["ret20"] > 0.015) and (sector["ma20_slope"] > 0))
        leader_strong = _bool((leader["close"] > leader["ma20"]) and (leader["ret20"] >= sector["ret20"]))
        # === 放宽金叉窗口：过去 N 天内任意一天上穿 MA20 都算"刚金叉"（默认 3 天）===
        cross_window = self.settings.cross_window
        just_crossed = False
        if len(bars_i) > cross_window:
            close_window = bars_i["close"].iloc[-cross_window - 1:].values
            ma20_window = bars_i["ma20"].iloc[-cross_window - 1:].values
            if (close_window <= ma20_window).any() and _bool(latest["close"] > latest["ma20"]):
                just_crossed = True
        if not just_crossed:
            # 兼容单日金叉（保持向后兼容）
            just_crossed = _bool((previous["close"] <= previous["ma20"] and latest["close"] > latest["ma20"]) or (latest["distance_ma20"] <= self.settings.max_distance_from_ma20 and latest["close"] > latest["ma20"]))
        # === 新增：趋势确认（已站上 MA20 持续 N 天）===
        above_ma20_days = 0
        for i in range(len(bars_i) - 1, -1, -1):
            row = bars_i.iloc[i]
            if pd.isna(row["ma20"]) or pd.isna(row["close"]):
                break
            if row["close"] > row["ma20"]:
                above_ma20_days += 1
            else:
                break
        trend_confirmed = (
            self.settings.allow_trend_following
            and above_ma20_days >= self.settings.trend_min_days_above_ma20
            and _bool(latest["close"] > latest["ma20"])
            and _bool(latest["distance_ma20"] <= self.settings.trend_max_distance_from_ma20)
        )
        low_position = _bool(latest["drawdown_from_60h"] <= -self.settings.low_position_drawdown_from_60d_high or latest["distance_ma20"] <= self.settings.max_distance_from_ma20)
        pnl_pct = None
        if cost_price and holding_shares > 0:
            pnl_pct = round(float(latest["close"] / cost_price - 1), 4)

        gates = {
            "market_above_ma20": market_above,
            "sector_strong": sector_strong,
            "leader_strong": leader_strong,
            "just_crossed_ma20": just_crossed,
            "trend_confirmed": trend_confirmed,
            "above_ma20_days": above_ma20_days,
            "low_position": low_position,
            "loss_streak": loss_streak,
            "holding_shares": holding_shares,
        }
        market_ok = market_above or not self.settings.require_market_above_ma20
        sector_ok = sector_strong or not self.settings.require_sector_strong
        # === 新增：板块弱时个股强势 → 仍可 TRIAL（板块放宽）===
        sector_relaxed_trial = (
            not sector_ok
            and self.settings.allow_sector_relaxed_trial
            and self.settings.allow_trial_position
            and _bool(latest["distance_ma20"] >= self.settings.sector_relaxed_min_distance)
            and market_ok
            and trend_confirmed
        )
        gates["sector_relaxed_trial"] = sector_relaxed_trial

        # === EXIT_ALERT 抖动过滤：below_ma20 需持续 N 天 ===
        below_ma20_streak = 0
        for i in range(len(bars_i) - 1, -1, -1):
            row = bars_i.iloc[i]
            if pd.isna(row["ma20"]) or pd.isna(row["close"]):
                break
            if row["close"] < row["ma20"]:
                below_ma20_streak += 1
            else:
                break
        # === EXIT_ALERT 抖动过滤：盈/亏太小不算 ===
        # 例如：浮盈 0.7% 不算达到 15% 止盈位；浮亏 0.5% 不算 10% 止损位
        # 但如果 take_profit_pct=0.15 且 pnl=0.7%，那不算 profit_alert
        # 如果 stop_loss_pct=-0.10 且 pnl=-0.5%，也不算 risk_alert
        min_pnl_abs = self.settings.min_exit_pnl_abs
        profit_alert = bool(
            pnl_pct is not None
            and pnl_pct >= self.settings.take_profit_pct
            and pnl_pct >= min_pnl_abs  # 必须达到配置 + 最小阈值
        )
        risk_alert = bool(
            pnl_pct is not None
            and pnl_pct <= self.settings.stop_loss_pct
            and abs(pnl_pct) >= min_pnl_abs
        )
        below_ma20_alert = bool(
            latest["close"] < latest["ma20"]
            and below_ma20_streak >= self.settings.below_ma20_min_days
        )
        # === sector_weak 不再触发 EXIT_ALERT ===
        # 板块弱只影响 NORMAL 路径（不开新仓），不应让已有持仓恐慌性退出
        reasons: list[str] = []
        blocked: list[str] = []
        exit_signal = {
            "profit_alert": profit_alert,
            "risk_alert": risk_alert,
            "below_ma20": below_ma20_alert,
            "below_ma20_streak": below_ma20_streak,
            "sector_weak": not sector_strong,  # 保留字段给 UI 参考，不参与 EXIT_ALERT
        }

        action = "SKIP"
        signal_type = "WATCH"
        if self.settings.enable_loss_streak_cooldown and loss_streak >= self.settings.cooldown_loss_count:
            action = "STOP"
            signal_type = "RISK"
            blocked.append(MSG_LOSS_STREAK_COOLDOWN)
        elif not market_ok:
            blocked.append(MSG_MARKET_BELOW_MA20)
        elif holding_shares > 0 and any([exit_signal["profit_alert"], exit_signal["risk_alert"], exit_signal["below_ma20"]]):
            action = "EXIT_ALERT"
            signal_type = "SELL"
            reasons.append(MSG_POSITION_EXIT_TRIGGERED)
        elif sector_ok and just_crossed and leader_strong and low_position and self.settings.allow_normal_position:
            action = "NORMAL"
            signal_type = "BUY"
            if market_above and sector_strong:
                reasons.append(MSG_MARKET_SECTOR_STOCK_RESONANT)
            else:
                reasons.append(MSG_FILTERS_RELAXED)
            reasons.extend([MSG_LEADER_STRONGER_THAN_SECTOR, MSG_STOCK_LOW_POSITION_CROSS_MA20])
        # === 新路径 A：板块强 + 趋势确认（不要求刚金叉）→ NORMAL ===
        elif sector_ok and trend_confirmed and leader_strong and self.settings.allow_normal_position:
            action = "NORMAL"
            signal_type = "BUY"
            reasons.append(MSG_STOCK_TREND_FOLLOWING)
            if not just_crossed:
                reasons.append(MSG_LEADER_STRONGER_THAN_SECTOR)
        # === 新路径 B：板块强 + 趋势确认（无 leader 强）→ TRIAL ===
        elif sector_ok and trend_confirmed and self.settings.allow_trial_position:
            action = "TRIAL"
            signal_type = "BUY"
            reasons.append(MSG_STOCK_TREND_FOLLOWING)
        elif sector_ok and just_crossed and self.settings.allow_trial_position:
            action = "TRIAL"
            signal_type = "BUY"
            reasons.append(MSG_STOCK_JUST_CROSSED_MA20)
        # === 新路径 C：板块弱 + 个股强势 → TRIAL（板块放宽）===
        elif sector_relaxed_trial:
            action = "TRIAL"
            signal_type = "BUY"
            reasons.append(MSG_STOCK_SECTOR_RELAXED_TRIAL)
        else:
            # 明确告诉用户卡在哪：板块弱 vs 个股条件不足
            if not sector_ok:
                blocked.append(MSG_SECTOR_NOT_RESONANT)
            else:
                blocked.append(MSG_STOCK_NOT_CROSSED_OR_RESONANT)

        score = 0
        # 二元门槛（基础分）
        score += 18 if market_above else 0
        score += 24 if sector_strong else 0
        score += 18 if leader_strong else 0
        score += 20 if just_crossed else 0
        score += 12 if low_position else 0
        # 连续分（让评分有"区分度"，不同标的得分不同）
        # 站上 MA20 持续天数：3 天 8 分，5 天 14 分，10 天 20 分，20 天 25 分（封顶）
        if above_ma20_days >= 20:
            score += 25
        elif above_ma20_days >= 10:
            score += 20
        elif above_ma20_days >= 5:
            score += 14
        elif above_ma20_days >= 3:
            score += 8
        # 距离 MA20 偏离：0-3% 给 8 分，3-6% 给 5 分，6-10% 给 2 分，>10% 给 0 分（防追高）
        dist = latest.get("distance_ma20")
        if pd.notna(dist):
            ad = abs(float(dist))
            if ad <= 0.03:
                score += 8
            elif ad <= 0.06:
                score += 5
            elif ad <= 0.10:
                score += 2
            # > 10% 不加分（可能追高）
        # 亏损扣分
        score -= min(loss_streak, 3) * 8
        score = max(0, min(score, 100))

        entry_signal = {
            "distance_ma20": round(float(latest["distance_ma20"]), 4),
            "ret15": round(float(latest["ret15"]), 4) if not pd.isna(latest["ret15"]) else None,
            "ret20": round(float(latest["ret20"]), 4) if not pd.isna(latest["ret20"]) else None,
            "drawdown_from_60h": round(float(latest["drawdown_from_60h"]), 4) if not pd.isna(latest["drawdown_from_60h"]) else None,
        }

        return Signal(
            code=code,
            name=name,
            signal_date=str(latest["trade_date"]),
            signal_type=signal_type,
            action=action,
            score=score,
            estimated_win_rate=estimate_win_rate(gates, action, loss_streak),
            reasons=reasons,
            blocked_reasons=blocked,
            gate_state=gates,
            entry_signal=entry_signal,
            exit_signal=exit_signal,
            pnl_pct=pnl_pct,
            holding_shares=holding_shares,
            cost_price=cost_price,
        )
