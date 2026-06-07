from __future__ import annotations

import json
import time
from dataclasses import asdict, fields
from datetime import date, datetime
from typing import Optional
from typing import Optional

import pandas as pd

from alphapilot.backtest.engine import Trend20Backtester
from alphapilot.config import (
    BENCHMARKS,
    BENCHMARK_CANDIDATES,
    DEFAULT_DB_PATH,
    DEFAULT_SIGNAL_LIMIT,
    MAX_SIGNAL_LIMIT,
    SIGNAL_PAGE_SIZE,
    WATCHLIST,
    Instrument,
    get_instrument,
)
from alphapilot.data.bootstrap import initialize_fund_flow_cache, initialize_market_cache
from alphapilot.data.cache import MarketDataCache
from alphapilot.i18n import (
    CACHE_MSG_EMPTY,
    CACHE_MSG_FAILED,
    CACHE_MSG_OK,
    CACHE_MSG_PARTIAL,
    CACHE_MSG_UPDATING,
)
from alphapilot.journal.store import JournalStore
from alphapilot.strategy.base import get_engine
from alphapilot.strategy.cache import IndicatorCache
from alphapilot.strategy.trend20 import Trend20Engine, Trend20Settings


TREND20_SETTINGS_KEY = "trend20_settings"


class TTLCache:
    """轻量 TTL 缓存(2026-06-07 加,服务 dashboard / backtest / next_session 减少重复计算)。

    - 单进程内存缓存(不跨进程,不持久化)
    - 默认 5s TTL:够抵抗浏览器连发 3-5 个请求,不会让用户感觉数据陈旧
    - 用法: `self._ttl_cache.get_or_compute("dashboard", 5, self._build_dashboard)`
    - 显式失效: `self._ttl_cache.invalidate("dashboard")`(数据 refresh 后调用)
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> Optional[object]:
        if key not in self._store:
            return None
        expires_at, value = self._store[key]
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)

    def get_or_compute(self, key: str, ttl_seconds: float, compute_fn) -> object:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value, ttl_seconds)
        return value

    def invalidate(self, key: Optional[str] = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)


class AlphaPilotService:
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.cache = MarketDataCache(db_path)
        self.journal = JournalStore(self.cache)
        self.engine = Trend20Engine()
        # provider 引用为可选,用于 quote() 在盘中拿到 intraday snapshot
        # 注入方式: service.provider = provider_by_name("akshare")
        self.provider = None  # type: ignore[assignment]
        # 2026-06-07: dashboard / backtest / next_session 共享 TTL 缓存
        self._ttl_cache = TTLCache()

    def set_provider(self, provider_name: str = "akshare") -> None:
        """注入 provider,启用 quote() 的盘中实时价(2026-06-07 加)。"""
        from alphapilot.data.providers import provider_by_name
        self.provider = provider_by_name(provider_name)

    def ensure_initialized(self) -> None:
        if not self.cache.has_bars():
            initialize_market_cache(self.cache, provider_name="sample", universe_name="watchlist", years=3)

    def initialize_data(
        self,
        provider: str = "sample",
        universe: str = "watchlist",
        years: int = 3,
        request_interval_seconds: Optional[float] = None,
        resume: bool = False,
        incomplete_only: bool = False,
        incremental: bool = False,
        max_symbols: Optional[int] = None,
    ) -> dict:
        return initialize_market_cache(
            self.cache,
            provider_name=provider,
            universe_name=universe,
            years=years,
            request_interval_seconds=request_interval_seconds,
            resume=resume,
            incomplete_only=incomplete_only,
            incremental=incremental,
            max_symbols=max_symbols,
        )

    def initialize_fund_flow_data(
        self,
        provider: str = "eastmoney_fund_flow",
        universe: str = "watchlist",
        years: int = 3,
        days: Optional[int] = None,
        request_interval_seconds: Optional[float] = None,
        resume: bool = False,
        incomplete_only: bool = False,
        max_symbols: Optional[int] = None,
    ) -> dict:
        return initialize_fund_flow_cache(
            self.cache,
            provider_name=provider,
            universe_name=universe,
            years=years,
            days=days,
            request_interval_seconds=request_interval_seconds,
            resume=resume,
            incomplete_only=incomplete_only,
            max_symbols=max_symbols,
        )

    def cache_status(self) -> dict:
        return self.cache.cache_status()

    def incremental_update(
        self,
        provider: str = "auto",
        universe: str = "watchlist",
        request_interval_seconds: Optional[float] = None,
    ) -> dict:
        """一键增量更新：只拉 last_trade_date+1 到今天；盘中使用实时快照覆盖当日。"""
        return self.initialize_data(
            provider=provider,
            universe=universe,
            resume=False,
            incomplete_only=False,
            incremental=True,
            request_interval_seconds=request_interval_seconds,
        )

    def trend20_settings(self) -> Trend20Settings:
        stored = self.cache.get_setting(TREND20_SETTINGS_KEY) or {}
        return _settings_from_payload(stored)

    def strategy_config(self) -> dict:
        return {
            "settings": asdict(self.trend20_settings()),
            "defaults": asdict(Trend20Settings()),
        }

    def update_strategy_config(self, payload: dict) -> dict:
        settings = _settings_from_payload(payload)
        data = asdict(settings)
        self.cache.set_setting(TREND20_SETTINGS_KEY, data)
        return {"settings": data, "defaults": asdict(Trend20Settings())}

    def reset_strategy_config(self) -> dict:
        """删除 DB 中保存的 Trend20Settings，让后续读取回退到默认。

        用途：手抖/测试把 settings 误改全 false 后，一键恢复。
        """
        self.cache.delete_setting(TREND20_SETTINGS_KEY)
        return {"settings": asdict(Trend20Settings()), "defaults": asdict(Trend20Settings()), "reset": True}

    PRESETS: dict[str, dict] = {
        "conservative": {
            "label": "保守（严格共振）",
            "description": "要求大盘+板块+龙头+刚金叉+低位全满足才出 NORMAL，TRIAL 也很少。信号少但质量高。",
            "overrides": {
                "require_market_above_ma20": True,
                "require_sector_strong": True,
                "allow_normal_position": True,
                "allow_trial_position": True,
                "allow_trend_following": False,
                "allow_sector_relaxed_trial": False,
                "cross_window": 1,
                "trend_min_days_above_ma20": 3,
                "trend_max_distance_from_ma20": 0.06,
                "sector_relaxed_min_distance": 0.05,
                "cooldown_loss_count": 2,
                "cooldown_days": 14,
            },
        },
        "standard": {
            "label": "标准（推荐）",
            "description": "默认设置：板块弱不阻塞开仓，但要求趋势确认（站上 MA20 持续 3 天）。震荡市也能拿到 TRIAL。",
            "overrides": {},  # 使用 Trend20Settings() 默认
        },
        "aggressive": {
            "label": "激进（多信号）",
            "description": "放宽所有过滤，金叉窗口放大到 7 天，允许试错，盈亏阈值小。信号多但假信号也多。",
            "overrides": {
                "require_market_above_ma20": False,
                "require_sector_strong": False,
                "allow_normal_position": True,
                "allow_trial_position": True,
                "allow_trend_following": True,
                "allow_sector_relaxed_trial": True,
                "cross_window": 7,
                "trend_min_days_above_ma20": 2,
                "trend_max_distance_from_ma20": 0.20,
                "sector_relaxed_min_distance": 0.01,
                "cooldown_loss_count": 5,
                "cooldown_days": 5,
                "min_exit_pnl_abs": 0.01,
                "below_ma20_min_days": 5,
            },
        },
    }

    def apply_preset(self, preset_name: str) -> dict:
        """应用 3 套预设之一，写入 DB。

        preset_name ∈ {"conservative", "standard", "aggressive"}
        "standard" 等价于 reset_strategy_config（使用默认）。
        """
        if preset_name not in self.PRESETS:
            raise ValueError(f"unknown preset: {preset_name}; available: {list(self.PRESETS)}")
        if preset_name == "standard":
            # 用默认：直接 reset
            return self.reset_strategy_config()
        overrides = self.PRESETS[preset_name]["overrides"]
        # 合并默认 + overrides，set 进去
        settings = Trend20Settings(**overrides)
        self.cache.set_setting(TREND20_SETTINGS_KEY, asdict(settings))
        return {
            "settings": asdict(settings),
            "defaults": asdict(Trend20Settings()),
            "preset": preset_name,
            "label": self.PRESETS[preset_name]["label"],
        }

    def list_presets(self) -> list[dict]:
        return [
            {"name": key, "label": v["label"], "description": v["description"]}
            for key, v in self.PRESETS.items()
        ]

    def signal_universes(self) -> list[dict]:
        instruments = [
            Instrument(item["symbol"], item["name"], item["asset_type"], item["sector"])
            for item in self.cache.list_instruments()
            if item["asset_type"] == "stock"
        ]
        # watchlist 计数 = 内置 WATCHLIST + 用户自选股去重
        user_picks = self.cache.list_watchlist()
        user_symbols = {p["symbol"] for p in user_picks}
        watchlist_count = len(WATCHLIST) + sum(1 for s in user_symbols if s not in {w.symbol for w in WATCHLIST})
        counts = {
            "watchlist": watchlist_count,
            "all_a": len(instruments),
            "main_board": sum(1 for item in instruments if _signal_board(item.symbol) == "main_board"),
            "chinext": sum(1 for item in instruments if _signal_board(item.symbol) == "chinext"),
            "star": sum(1 for item in instruments if _signal_board(item.symbol) == "star"),
            "bj": sum(1 for item in instruments if _signal_board(item.symbol) == "bj"),
        }
        options = [
            ("watchlist", "策略池", "用户当前重点观察标的"),
            ("all_a", "全部 A 股", "本地缓存中的全部股票"),
            ("main_board", "沪深主板", "沪深主板股票"),
            ("chinext", "创业板", "创业板股票"),
            ("star", "科创板", "科创板股票"),
            ("bj", "北交所", "北交所股票"),
        ]
        return [
            {"value": value, "label": label, "description": description, "count": counts.get(value, 0)}
            for value, label, description in options
        ]

    def data_status(self) -> dict:
        """数据状态总览(独立调用入口,内部复用 cache_status 读 1 次)"""
        return self.data_status_from(cache_status=self.cache.lightweight_cache_status())

    def data_status_from(self, cache_status: dict) -> dict:
        """可注入 cache_status 快照,避免与外部调用方 race。

        新增 fund_flow.status 枚举(2026-06-07):
            - "ok"     本次 fetch 成功或有缓存可用
            - "missing  无任何缓存(count=0 且 latest_trade_date 为空)
            - "failed"  最近一次 fetch 全部失败(failure_count>0 && success_count==0)
            - "stale"   缓存存在但最新日期距今 > 3 天(可能 fetch 部分成功但未更新到今天)
        """
        self.ensure_initialized()
        cache = cache_status
        instruments = [*BENCHMARKS.values(), *WATCHLIST]
        provider_mix: dict[str, int] = {}
        symbol_sources = []
        fetch_statuses = {item["symbol"]: item for item in self.cache.fetch_symbol_statuses()}
        for instrument in instruments:
            provider = self.cache.latest_provider(instrument.symbol) or "missing"
            fetch_status = fetch_statuses.get(instrument.symbol, {})
            provider_mix[provider] = provider_mix.get(provider, 0) + 1
            symbol_sources.append(
                {
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "provider": provider,
                    "latest_trade_date": self.cache.latest_trade_date(instrument.symbol),
                    "last_fetch_status": fetch_status.get("status", "UNKNOWN"),
                    "last_fetch_provider": fetch_status.get("provider"),
                    "last_fetch_message": fetch_status.get("message"),
                    "last_fetch_at": fetch_status.get("attempted_at"),
                }
            )

        last_run = cache.get("last_fetch_run") or {}
        errors = _parse_errors(last_run.get("errors_json"))
        fund_flow_run = cache.get("last_fund_flow_fetch_run") or {}
        fund_flow_errors = _parse_errors(fund_flow_run.get("errors_json"))
        status = "OK"
        message = CACHE_MSG_OK
        if cache["bar_count"] == 0:
            status = "EMPTY"
            message = CACHE_MSG_EMPTY
        elif last_run.get("status") == "FAILED" or (
            int(last_run.get("success_count") or 0) == 0 and int(last_run.get("failure_count") or 0) > 0
        ):
            status = "FAILED"
            message = CACHE_MSG_FAILED
        elif last_run.get("status") == "PARTIAL":
            status = "PARTIAL"
            message = CACHE_MSG_PARTIAL
        elif last_run.get("status") == "RUNNING":
            status = "UPDATING"
            message = CACHE_MSG_UPDATING

        # 数据新鲜度：仅基于 watchlist + benchmarks 的最新数据日期
        tracked_symbols = {inst.symbol for inst in [*BENCHMARKS.values(), *WATCHLIST]}
        tracked_fetch_statuses = {
            sym: fetch_statuses[sym] for sym in tracked_symbols if sym in fetch_statuses
        }
        freshness = _data_freshness_summary(tracked_fetch_statuses)

        # 资金流状态机: ok / failed / missing / stale
        ff_count = cache.get("fund_flow_count", 0)
        ff_latest = cache.get("fund_flow_latest_trade_date")
        ff_status = "ok"
        if ff_count == 0 and not ff_latest:
            ff_status = "missing"
        elif (fund_flow_run.get("status") == "FAILED"
              or (int(fund_flow_run.get("failure_count") or 0) > 0
                  and int(fund_flow_run.get("success_count") or 0) == 0)):
            ff_status = "failed"
        elif ff_latest and _is_stale(ff_latest, days=3):
            ff_status = "stale"

        return {
            "status": status,
            "message": message,
            "latest_trade_date": cache.get("latest_trade_date"),
            "bar_count": cache.get("bar_count"),
            "symbol_count": cache.get("symbol_count"),
            "provider_mix": provider_mix,
            "freshness": freshness,
            "last_fetch": {
                "provider": last_run.get("provider"),
                "status": last_run.get("status"),
                "success_count": last_run.get("success_count", 0),
                "failure_count": last_run.get("failure_count", 0),
                "start_date": last_run.get("start_date"),
                "end_date": last_run.get("end_date"),
                "started_at": last_run.get("started_at"),
                "ended_at": last_run.get("ended_at"),
                "errors": errors,
            },
            "fund_flow": {
                "status": ff_status,  # 枚举: ok / failed / missing / stale
                "count": ff_count,
                "symbol_count": cache.get("fund_flow_symbol_count", 0),
                "latest_trade_date": ff_latest,
                "last_fetch": {
                    "provider": fund_flow_run.get("provider"),
                    "status": fund_flow_run.get("status"),
                    "success_count": fund_flow_run.get("success_count", 0),
                    "failure_count": fund_flow_run.get("failure_count", 0),
                    "start_date": fund_flow_run.get("start_date"),
                    "end_date": fund_flow_run.get("end_date"),
                    "started_at": fund_flow_run.get("started_at"),
                    "ended_at": fund_flow_run.get("ended_at"),
                    "errors": fund_flow_errors,
                },
            },
            "symbol_sources": symbol_sources,
        }

    def benchmark_cards(self) -> list[dict]:
        cards = []
        for key, instrument in BENCHMARKS.items():
            bars = self.cache.get_bars(instrument.symbol)
            if bars is None or len(bars) < 2:
                cards.append(
                    {
                        "key": key,
                        "symbol": instrument.symbol,
                        "name": instrument.name,
                        "value": None,
                        "change_pct": 0.0,
                        "ma20": None,
                        "ma60": None,
                        "dist_ma20_pct": None,
                        "dist_ma60_pct": None,
                        "ma20_slope": None,
                        "volume_ratio": None,
                        "state": "无数据",
                    }
                )
                continue
            latest = _latest_bar(bars)
            previous = bars.iloc[-2] if len(bars) >= 2 else None
            change = 0.0
            if latest is not None and previous is not None:
                change = float(latest["close"] / previous["close"] - 1)
            close = float(latest["close"]) if latest is not None else None
            ma20 = _ma(bars, 20)
            ma60 = _ma(bars, 60)
            dist_ma20 = (close / ma20 - 1) if (close is not None and ma20 is not None) else None
            dist_ma60 = (close / ma60 - 1) if (close is not None and ma60 is not None) else None
            # MA20 斜率 = 今日 MA20 / 5 日前 MA20 - 1（5 个交易日 ≈ 1 周，足够过滤日线噪音）
            slope = _ma_slope(bars, 20, lookback=5)
            vol_ratio = _volume_ratio(bars, lookback=5)
            cards.append(
                {
                    "key": key,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "value": round(close, 3) if close is not None else None,
                    "change_pct": round(change, 4),
                    "ma20": round(ma20, 3) if ma20 is not None else None,
                    "ma60": round(ma60, 3) if ma60 is not None else None,
                    "dist_ma20_pct": round(dist_ma20, 4) if dist_ma20 is not None else None,
                    "dist_ma60_pct": round(dist_ma60, 4) if dist_ma60 is not None else None,
                    "ma20_slope": round(slope, 4) if slope is not None else None,
                    "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
                    "state": _benchmark_state(close, ma20, ma60, slope, dist_ma20),
                }
            )
        return cards

    def benchmark_options(self) -> dict[str, list[dict]]:
        """返回三个槽位各自的候选准基（含真实最新价/涨跌幅），供前端切换。

        标记 has_data=False 表示该候选本地未缓存，UI 应禁用或显示提示。
        """
        result: dict[str, list[dict]] = {}
        for key, instruments in BENCHMARK_CANDIDATES.items():
            cards = []
            for inst in instruments:
                bars = self.cache.get_bars(inst.symbol)
                has_data = bars is not None and len(bars) >= 2
                if not has_data:
                    cards.append(
                        {
                            "key": key,
                            "symbol": inst.symbol,
                            "name": inst.name,
                            "value": None,
                            "change_pct": 0.0,
                            "has_data": False,
                        }
                    )
                    continue
                latest = _latest_bar(bars)
                previous = bars.iloc[-2] if len(bars) >= 2 else None
                change = 0.0
                if latest is not None and previous is not None:
                    change = float(latest["close"] / previous["close"] - 1)
                cards.append(
                    {
                        "key": key,
                        "symbol": inst.symbol,
                        "name": inst.name,
                        "value": round(float(latest["close"]), 3) if latest is not None else None,
                        "change_pct": round(change, 4),
                        "has_data": True,
                    }
                )
            result[key] = cards
        return result

    def quote(self, code: str) -> dict:
        """返回单只标的最新一根 K 线的价格信息,供"标记买入"表单一键填价使用。

        2026-06-07 优化: 盘中(market_open=True)时,优先读 provider 的 intraday snapshot,
        fallback 才用 daily_bars.iloc[-1]。这样 9:30-15:00 之间的最新价是最新的,
        而不是昨日收盘。
        """
        # 1) 盘中实时快照(优先)
        if self.provider is not None and self._is_market_open_now():
            snap = self._fetch_intraday_snapshot(code)
            if snap and snap.get("close"):
                # 用昨日收盘算 change_pct
                bars = self.cache.get_bars(code)
                prev_close = float(bars["close"].iloc[-1]) if bars is not None and len(bars) > 0 else snap["close"]
                change_pct = (snap["close"] / prev_close - 1) if prev_close > 0 else 0.0
                return {
                    "code": code,
                    "last_price": round(float(snap["close"]), 3),
                    "change_pct": round(float(change_pct), 4),
                    "trade_date": snap.get("trade_date", date.today().isoformat()),
                    "has_data": True,
                    "is_intraday": True,
                }
        # 2) 日线最后一行(fallback)
        bars = self.cache.get_bars(code)
        if bars is None or len(bars) == 0:
            return {"code": code, "last_price": None, "change_pct": None, "trade_date": None, "has_data": False}
        last = bars.iloc[-1]
        prev = bars.iloc[-2] if len(bars) >= 2 else None
        last_close = float(last["close"])
        prev_close = float(prev["close"]) if prev is not None else last_close
        change_pct = (last_close / prev_close - 1) if prev_close > 0 else 0.0
        return {
            "code": code,
            "last_price": round(last_close, 3),
            "change_pct": round(change_pct, 4),
            "trade_date": str(last.get("date", "")),
            "has_data": True,
            "is_intraday": False,
        }

    def _is_market_open_now(self) -> bool:
        """盘中判定:工作日 9:30-15:00。简化版,仅作为 hint,实际精度由 bootstrap.is_market_open 决定。"""
        from datetime import datetime, time
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(15, 0)

    def _fetch_intraday_snapshot(self, code: str) -> Optional[dict]:
        """尝试拿 intraday snapshot。失败返回 None。"""
        if self.provider is None:
            return None
        snap_fn = getattr(self.provider, "fetch_intraday_snapshot", None)
        if snap_fn is None:
            return None
        try:
            inst = get_instrument(code)
            return snap_fn(inst)
        except Exception:
            return None

    def signals(
        self,
        as_of: Optional[str] = None,
        universe: str = "watchlist",
        limit: Optional[int] = None,
        market: Optional[str] = None,
        style: Optional[str] = None,
        sector: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> list[dict]:
        instruments = self._signal_instruments(universe, keyword=keyword)
        if limit is None and universe != "watchlist" and not keyword:
            limit = DEFAULT_SIGNAL_LIMIT
        if limit is not None:
            limit = max(1, min(int(limit), MAX_SIGNAL_LIMIT))
            instruments = instruments[:limit]
        return self._signals_for_instruments(
            instruments, as_of=as_of,
            market_symbol=market, style_symbol=style, sector_symbol=sector,
        )

    def signal_page(
        self,
        as_of: Optional[str] = None,
        universe: str = "watchlist",
        page: int = 1,
        page_size: int = SIGNAL_PAGE_SIZE,
        market: Optional[str] = None,
        style: Optional[str] = None,
        sector: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> dict:
        instruments = self._signal_instruments(universe, keyword=keyword)
        page_size = max(1, min(int(page_size), SIGNAL_PAGE_SIZE))
        total = len(instruments)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), total_pages))
        start = (page - 1) * page_size
        end = start + page_size
        rows = self._signals_for_instruments(
            instruments[start:end], as_of=as_of,
            market_symbol=market, style_symbol=style, sector_symbol=sector,
        )
        return {
            "rows": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "universe": universe,
        }

    def _signals_for_instruments(
        self,
        instruments: list[Instrument],
        as_of: Optional[str] = None,
        market_symbol: Optional[str] = None,
        style_symbol: Optional[str] = None,
        sector_symbol: Optional[str] = None,
    ) -> list[dict]:
        self.ensure_initialized()

        def _resolve(slot: str, requested: Optional[str]) -> str:
            """把前端传来的 symbol 解析成"有缓存数据"的真实 symbol。

            候选若无数据则回退到默认准基，避免信号全部变 SKIP。
            """
            if requested and len(self.cache.get_bars(requested)) >= 2:
                return requested
            return BENCHMARKS[slot].symbol

        market_sym = _resolve("market", market_symbol)
        style_sym = _resolve("style", style_symbol)
        sector_sym = _resolve("sector", sector_symbol)
        market = self.cache.get_bars(market_sym)
        sector = self.cache.get_bars(sector_sym)
        # style 槽位用来覆盖 leader_bars：用户切到哪个风格指数，
        # 就用那个指数作为"龙头/风格强度"的参考（默认是 601138.SH）
        leader = self.cache.get_bars(style_sym)
        # 用 IndicatorCache 复用同一份 bars 的指标计算
        indicator_cache = IndicatorCache()
        market_i = indicator_cache.get(market, as_of=as_of)
        sector_i = indicator_cache.get(sector, as_of=as_of)
        leader_i = indicator_cache.get(leader, as_of=as_of) if leader is not None and not leader.empty else None
        holdings = self.journal.holdings()
        outputs = []
        loss_streak = self._loss_streak()
        engine = get_engine("trend20", self.trend20_settings())
        bars_by_symbol = self.cache.get_bars_many(
            [instrument.symbol for instrument in instruments],
            end_date=as_of,
            limit_rows=90,
        )
        for instrument in instruments:
            bars = bars_by_symbol.get(instrument.symbol, pd.DataFrame())
            if bars.empty:
                continue
            holding = holdings.get(instrument.symbol, {})
            signal = engine.evaluate(
                code=instrument.symbol,
                name=instrument.name,
                bars=bars,
                market_bars=market,
                sector_bars=sector,
                leader_bars=leader if not leader.empty else None,
                market_indicators=market_i,
                sector_indicators=sector_i,
                leader_indicators=leader_i,
                as_of=as_of,
                loss_streak=loss_streak,
                holding_shares=int(holding.get("shares", 0)),
                cost_price=float(holding.get("cost", 0.0)) or None,
            )
            data = signal.to_dict()
            latest = _latest_bar(bars)
            previous = bars.iloc[-2] if len(bars) >= 2 else None
            data["last_price"] = round(float(latest["close"]), 3) if latest is not None else None
            data["change_pct"] = round(float(latest["close"] / previous["close"] - 1), 4) if latest is not None and previous is not None else None
            data["sector"] = instrument.sector
            data["board"] = _signal_board_label(instrument.symbol)
            data["reason_text"] = "；".join(data["reasons"] or data["blocked_reasons"])
            outputs.append(data)
        return outputs

    def _signal_instruments(self, universe: str, keyword: Optional[str] = None) -> list[Instrument]:
        kw = (keyword or "").strip().lower()

        def _match(inst: Instrument) -> bool:
            if not kw:
                return True
            return kw in inst.symbol.lower() or kw in inst.name.lower()

        if universe == "watchlist":
            # 合并 WATCHLIST + 用户自选股
            user_picks = [
                Instrument(item["symbol"], item["name"], item["asset_type"], item["sector"])
                for item in self.cache.list_watchlist()
            ]
            merged: list[Instrument] = []
            seen: set[str] = set()
            for inst in list(WATCHLIST) + user_picks:
                if inst.symbol not in seen:
                    seen.add(inst.symbol)
                    merged.append(inst)
            return [inst for inst in merged if _match(inst)]

        stocks = [
            Instrument(item["symbol"], item["name"], item["asset_type"], item["sector"])
            for item in self.cache.list_instruments()
            if item["asset_type"] == "stock"
        ]
        if universe == "all_a":
            return [inst for inst in stocks if _match(inst)]
        if universe in {"main_board", "chinext", "star", "bj"}:
            return [inst for inst in stocks if _signal_board(inst.symbol) == universe and _match(inst)]
        if universe.startswith("sector:"):
            sector_name = universe.split(":", 1)[1]
            return [inst for inst in stocks if inst.sector == sector_name and _match(inst)]
        return [inst for inst in WATCHLIST if _match(inst)]

    def add_to_watchlist(self, symbol: str, name: str, sector: str = "") -> dict:
        return {
            "added": self.cache.add_to_watchlist(symbol, name, sector=sector),
            "watchlist_count": len(self.cache.list_watchlist()),
        }

    def remove_from_watchlist(self, symbol: str) -> dict:
        return {
            "removed": self.cache.remove_from_watchlist(symbol),
            "watchlist_count": len(self.cache.list_watchlist()),
        }

    def watchlist(self) -> list[dict]:
        return self.cache.list_watchlist()

    def all_instruments(self) -> list[dict]:
        """返回全市场标的（来自 instruments 表 + 已有 K 线的标的）。

        用于 mark 表单的全市场搜索/选择——让用户能基于任何信号标的 mark。
        批量从 instruments 表取 name，避免逐个查询。
        """
        seen: set[str] = set()
        symbols: list[str] = []
        # 1) 已有 K 线的标的（来自 daily_bars）
        # 2) instruments 表 stock 类型
        with self.cache.connect() as conn:
            for q in ("select distinct symbol from daily_bars",
                      "select distinct symbol from instruments where asset_type = 'stock'"):
                for r in conn.execute(q).fetchall():
                    sym = r["symbol"]
                    if sym not in seen:
                        seen.add(sym)
                        symbols.append(sym)
        # 批量查 name
        names: dict[str, str] = {}
        with self.cache.connect() as conn:
            placeholders = ",".join("?" for _ in symbols)
            if placeholders:
                for r in conn.execute(
                    f"select symbol, name from instruments where symbol in ({placeholders})", symbols
                ).fetchall():
                    if r["name"]:
                        names[r["symbol"]] = r["name"]
        out: list[dict] = []
        for sym in symbols:
            inst = get_instrument(sym)
            db_name = names.get(sym)
            if db_name:
                inst = type(inst)(symbol=sym, name=db_name, asset_type=inst.asset_type, sector=inst.sector)
            out.append({"symbol": sym, "name": inst.name})
        return out

    def dashboard(self) -> dict:
        # 2026-06-07: 5s TTL,首屏 5s 内的多次请求复用同一份结果
        # 防御场景: 用户刷新页面 + dashboard 多个并行 fetch + 切 tab 反复拉取
        return self._ttl_cache.get_or_compute(
            "dashboard", ttl_seconds=5.0, compute_fn=self._build_dashboard
        )

    def _build_dashboard(self) -> dict:
        self.ensure_initialized()
        signals = self.signals()
        action_counts = {
            "NORMAL": sum(1 for item in signals if item["action"] == "NORMAL"),
            "TRIAL": sum(1 for item in signals if item["action"] == "TRIAL"),
            "EXIT_ALERT": sum(1 for item in signals if item["action"] == "EXIT_ALERT"),
            "SKIP": sum(1 for item in signals if item["action"] == "SKIP"),
            "STOP": sum(1 for item in signals if item["action"] == "STOP"),
        }
        # 持仓总览：基于 journal 标记 + 实时价计算
        portfolio = self._portfolio_summary()
        # 下一交易日：基于交易日历给出
        next_session = self._next_session_plan()
        # 同源数据：cache_status 只取一次,避免同一请求内 race(增量 refresh 在并发时会让两次读差几行)
        cache_status = self.cache.lightweight_cache_status()
        # 数据新鲜度（watchlist + benchmarks）
        data_status = self.data_status_from(cache_status=cache_status)
        freshness = data_status.get("freshness", {})
        # 基准卡片（同时给前端和 metrics 复用，避免重复算）
        benchmark_cards = self.benchmark_cards()
        benchmark_cards_dict = {c["key"]: c["state"] for c in benchmark_cards}
        return {
            "as_of": date.today().isoformat(),
            "benchmarks": benchmark_cards,
            "cache": cache_status,
            "data_status": data_status,
            "freshness": freshness,
            "metrics": {
                # 大盘过滤 = 大盘 state 在 {强, 过热} 视作"通过"（震荡视为勉强通过，弱为过滤）
                "market_filter": "通过" if _is_pass_state(benchmark_cards_dict.get("market", "无数据")) else "过滤",
                "sector_state": benchmark_cards_dict.get("sector", "无数据"),
                # 2026-06-07: market_state / style_state 已废弃,前端从 benchmarks 数组取
                # (暂时保留 sector_state 兼容老逻辑,下个版本可一起删)
                "loss_streak": self._loss_streak(),
                "action_counts": action_counts,
                "portfolio_pnl": self._portfolio_pnl_summary(),
            },
            "portfolio": portfolio,
            "next_session": next_session,
            "signals": signals,  # 全量交给前端分组
            "signals_grouped": _group_signals_for_dashboard(signals, self.cache.list_watchlist()),
            # §5.2 首页 Dashboard 三项核心：板块强度 / 持仓风险 / 策略表现
            "sector_ranking": self._sector_ranking(),
            "holding_risks": self._holding_risks(),
            "performance_curve": self._performance_curve(),
            "performance": {"win_rate": None},
            "factor_win_rates": {},
        }

    def _sector_ranking(self) -> dict:
        """对 19 个行业/主题 ETF 算状态,按强弱分 3 组。
        强 = close>MA20+MA60+slope>0;弱 = close<MA20+MA60+slope<0;其余观察。
        """
        strong, watch, weak = [], [], []
        for inst in BENCHMARK_CANDIDATES.get("sector", []):
            bars = self.cache.get_bars(inst.symbol)
            if bars is None or len(bars) < 60:
                continue
            close = float(bars["close"].iloc[-1])
            ma20 = _ma(bars, 20)
            ma60 = _ma(bars, 60)
            slope = _ma_slope(bars, 20, lookback=5)
            if close is None or ma20 is None or ma60 is None:
                continue
            dist_ma20 = close / ma20 - 1
            state = _benchmark_state(close, ma20, ma60, slope, dist_ma20)
            change = float(close / bars["close"].iloc[-2] - 1) if len(bars) >= 2 else 0.0
            entry = {
                "symbol": inst.symbol,
                "name": inst.name,
                "sector": inst.sector,
                "change_pct": round(change, 4),
                "dist_ma20_pct": round(dist_ma20, 4),
                "ma20_slope": round(slope, 4) if slope is not None else None,
            }
            if state == "强":
                strong.append(entry)
            elif state == "弱":
                weak.append(entry)
            else:
                watch.append(entry)
        # 强按涨幅降序,弱按跌幅升序(跌最多的最危险)
        strong.sort(key=lambda x: -x["change_pct"])
        watch.sort(key=lambda x: -x["change_pct"])
        weak.sort(key=lambda x: x["change_pct"])
        return {"strong": strong[:5], "watch": watch[:5], "weak": weak[:5]}

    def _holding_risks(self) -> list[dict]:
        """当前所有持仓的逐票状态：跌破 MA20 / 接近止盈(+15%) / 接近止损(-10%)。
        商业计划书 §5.2 持仓风险 + §6.4 卖出条件 S1-S5 的完整实现。
        无警报的持仓也返回（severity=ok），让"我的持仓"卡片显示全部仓位而非只显示触发警报的。
        """
        cfg = self.trend20_settings()
        take_profit_pct = cfg.take_profit_pct
        stop_loss_pct = cfg.stop_loss_pct
        holdings = []
        for mode in ("real", "paper"):
            for code, h in self.journal.holdings(mode=mode).items():
                if h["shares"] <= 0:
                    continue
                bars = self.cache.get_bars(code)
                if bars is None or len(bars) < 20:
                    # 数据不足仍展示，但标 ok
                    holdings.append({
                        "code": code,
                        "name": h["name"],
                        "mode": mode,
                        "shares": h["shares"],
                        "cost_price": round(float(h["cost"]), 3),
                        "last_price": None,
                        "pnl_pct": None,
                        "dist_ma20_pct": None,
                        "alerts": [],
                        "severity": "ok",
                    })
                    continue
                cost = float(h["cost"])
                last_price = float(bars["close"].iloc[-1])
                pnl_pct = (last_price / cost - 1) if cost > 0 else 0
                ma20 = _ma(bars, 20)
                dist_ma20 = (last_price / ma20 - 1) if ma20 else 0
                alerts = []
                severity = "ok"  # ok / warn / danger
                # stop_loss_pct 是负数阈值（如 -0.10）;pnl_pct 也是负数时 pnl_pct <= stop_loss_pct
                if pnl_pct >= take_profit_pct:
                    alerts.append(f"已触发止盈（+{pnl_pct*100:.1f}% ≥ +{take_profit_pct*100:.0f}%）")
                    severity = "warn"
                if pnl_pct <= stop_loss_pct:
                    alerts.append(f"已触发止损（{pnl_pct*100:.1f}% ≤ {stop_loss_pct*100:.0f}%）")
                    severity = "danger"
                elif pnl_pct < 0:
                    # 距离止损线还有多少
                    distance_pct = (stop_loss_pct - pnl_pct) * 100
                    alerts.append(f"未触发止损（当前 {pnl_pct*100:.1f}%，距止损线 {abs(distance_pct):.1f} 个百分点）")
                if dist_ma20 < -0.02:
                    alerts.append(f"跌破 MA20（{dist_ma20*100:.1f}%）")
                    if severity == "ok":
                        severity = "warn"
                elif pnl_pct < 0 and dist_ma20 < 0:
                    alerts.append(f"在 MA20 下方运行（{dist_ma20*100:.1f}%）")
                    if severity == "ok":
                        severity = "warn"
                holdings.append({
                    "code": code,
                    "name": h["name"],
                    "mode": mode,
                    "shares": h["shares"],
                    "cost_price": round(cost, 3),
                    "last_price": round(last_price, 3),
                    "pnl_pct": round(pnl_pct, 4),
                    "dist_ma20_pct": round(dist_ma20, 4),
                    "alerts": alerts,
                    "severity": severity,
                })
        # danger 优先,再 warn,最后 ok
        order = {"danger": 0, "warn": 1, "ok": 2}
        holdings.sort(key=lambda r: (order.get(r["severity"], 3), r["pnl_pct"] or 0))
        # 计算每只持仓的"成本市值"和"占总仓位比例",便于前端展示
        total_cost = sum(
            (h.get("cost_price") or 0) * h.get("shares", 0)
            for h in holdings
        ) or 1
        for h in holdings:
            cost_value = (h.get("cost_price") or 0) * h.get("shares", 0)
            h["cost_value"] = round(cost_value, 2)
            h["position_share_pct"] = round(cost_value / total_cost, 4) if total_cost > 0 else 0
        return holdings

    def _performance_curve(self) -> dict:
        """近 30 个交易日策略的日累计收益(基于回测 trade list)。
        对应商业计划书 §7.3 输出指标：总收益、最大回撤。
        """
        try:
            result = self.backtest()
        except Exception:
            return {"curve": [], "summary": {"total_return_pct": 0, "max_drawdown_pct": 0, "trade_count": 0}}
        trades = result.get("trades", []) or []
        if not trades:
            return {"curve": [], "summary": {"total_return_pct": 0, "max_drawdown_pct": 0, "trade_count": 0}}
        # 按 exit_date 排序,累计收益 = 累加 pnl_pct(简单求和,不考虑复利,因为 backtest 已是单标的 walk-forward)
        trades = sorted(trades, key=lambda t: t.get("exit_date", ""))
        curve = []
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cum += t.get("pnl_pct", 0)
            peak = max(peak, cum)
            dd = peak - cum  # 当前回撤
            max_dd = max(max_dd, dd)
            curve.append({
                "date": t.get("exit_date", ""),
                "cum_return_pct": round(cum, 4),
                "drawdown_pct": round(dd, 4),
            })
        return {
            "curve": curve[-30:],  # 最多 30 个点
            "summary": {
                "total_return_pct": round(cum, 4),
                "max_drawdown_pct": round(max_dd, 4),
                "trade_count": len(trades),
            },
        }

    def _portfolio_summary(self) -> dict:
        """根据 journal 标记 + 最新 K 线计算持仓总览。分别按 real/paper 算 PnL。"""
        result = {
            "real": self._portfolio_for_mode("real"),
            "paper": self._portfolio_for_mode("paper"),
        }
        # 兼容老格式：合并 total = real + paper（用于 dashboard 总览）
        result["position_count"] = len(result["real"]["positions"]) + len(result["paper"]["positions"])
        result["total_cost"] = round(result["real"]["total_cost"] + result["paper"]["total_cost"], 2)
        result["total_market_value"] = round(result["real"]["total_market_value"] + result["paper"]["total_market_value"], 2)
        result["total_unrealized_pnl_pct"] = round(
            (result["total_market_value"] / result["total_cost"] - 1) if result["total_cost"] > 0 else 0, 4
        )
        # realized：合并 real+paper 的 SELL 计数
        realized_pnl = 0.0
        realized_count = 0
        for mark in self.journal.list_marks():
            if mark["side"] == "SELL" and "亏损" in (mark.get("note") or ""):
                realized_pnl -= 1
                realized_count += 1
            elif mark["side"] == "SELL":
                realized_pnl += 1
                realized_count += 1
        result["realized_pnl_count"] = realized_count
        result["realized_pnl"] = round(realized_pnl, 4)
        result["positions"] = result["real"]["positions"]  # 兼容老调用：默认 real
        return result

    def _portfolio_for_mode(self, mode: str) -> dict:
        """按 mode（real/paper）单独算持仓 + PnL。"""
        holdings = self.journal.holdings(mode=mode)
        positions = []
        total_cost = 0.0
        total_market = 0.0
        for code, h in holdings.items():
            if h["shares"] <= 0:
                continue
            cost = float(h["cost"])
            cost_value = cost * h["shares"]
            bars = self.cache.get_bars(code)
            last_price = float(bars["close"].iloc[-1]) if bars is not None and not bars.empty else cost
            market_value = last_price * h["shares"]
            unrealized_pnl_pct = round((last_price / cost - 1) if cost > 0 else 0, 4)
            positions.append({
                "code": code,
                "name": h["name"],
                "shares": h["shares"],
                "cost_price": round(cost, 3),
                "last_price": round(last_price, 3),
                "market_value": round(market_value, 2),
                "unrealized_pnl_pct": unrealized_pnl_pct,
            })
            total_cost += cost_value
            total_market += market_value
        return {
            "position_count": len(positions),
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market, 2),
            "positions": positions,
        }

    def _portfolio_pnl_summary(self) -> dict:
        """汇总 real/paper 各自的浮动盈亏（% + 绝对值）。"""
        result = {}
        for mode in ("real", "paper"):
            sub = self._portfolio_for_mode(mode)
            cost = sub["total_cost"]
            market = sub["total_market_value"]
            pnl = market - cost
            pnl_pct = round((pnl / cost) if cost > 0 else 0, 4)
            result[mode] = {
                "position_count": sub["position_count"],
                "total_cost": cost,
                "total_market_value": market,
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_pct": pnl_pct,
            }
        # 合并（兼容 dashboard 显示）
        total_cost = result["real"]["total_cost"] + result["paper"]["total_cost"]
        total_market = result["real"]["total_market_value"] + result["paper"]["total_market_value"]
        total_pnl = total_market - total_cost
        result["total"] = {
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market, 2),
            "unrealized_pnl": round(total_pnl, 2),
            "unrealized_pnl_pct": round((total_pnl / total_cost) if total_cost > 0 else 0, 4),
        }
        return result

    def paper_equity_curve(self, end_date: Optional[str] = None) -> dict:
        """模拟持仓曲线：按日聚合 paper mark + 用每日 K 线收盘价回算 PnL 走势。

        算法：
        1. 取所有 paper mark，按时间正序
        2. 起始日期 = 最早一笔 paper mark 的 mark_date（若无 mark 则空）
        3. 截止日期 = end_date 或 today(2026-06-07 修:实际取 min(today, latest_trade_date),
           避免周末/节假日买入时 K 线缺失导致 curve 为空)
        4. 对每一天：
           - 还原当日开盘时的"持仓+成本"（用截至当日的 paper mark）
           - 计算当日市值 = sum(每只持仓 shares × 当日收盘价)
           - pnl = 市值 - 累计成本
        5. 返回折线数据：[{date, market_value, cost, pnl, pnl_pct}]
        """
        from datetime import datetime, timedelta
        marks = self.journal.list_marks(mode="paper")
        if not marks:
            return {"curve": [], "summary": {"trade_count": 0, "first_date": None, "last_date": None}}
        # 最早的 mark 日期
        marks_sorted = sorted(marks, key=lambda m: m["mark_date"])
        first_date = marks_sorted[0]["mark_date"]
        end = end_date or date.today().isoformat()
        if first_date > end:
            return {"curve": [], "summary": {"trade_count": len(marks), "first_date": first_date, "last_date": end}}

        # 2026-06-07 修: 用 sample provider 时,数据最新日期可能远早于今天(如几天前甚至更早)。
        # 而 mark_trade() 默认 mark_date = today,导致 first_date > latest_trade_date(虽然 first_date <= end 成立)
        # 把 first_date 也 clamp 到数据最新日期,保证 K 线覆盖了 mark 写入日。
        # 真实环境(provider=akshare/tencent)latest_trade_date == today,clamp 是 no-op。
        cache_status = self.cache.lightweight_cache_status()
        latest = cache_status.get("latest_trade_date")
        if latest:
            if first_date > latest:
                first_date = latest
            if not end_date and latest < end:
                end = latest

        # 收集所有涉及的代码
        codes = sorted({m["code"] for m in marks_sorted})
        # 预加载每日 K 线（按日期升序）
        bars_by_code: dict[str, list] = {}
        for code in codes:
            bars = self.cache.get_bars(code)
            if bars is None or bars.empty:
                continue
            # 只取 ≤ end 的部分
            bars = bars[bars["trade_date"] <= end]
            bars = bars.sort_values("trade_date")
            # 转成 date -> close 的 dict
            bars_by_code[code] = list(zip(bars["trade_date"].astype(str), bars["close"].astype(float)))

        # 生成日期序列（按 B 工作日，含节假日 + 周末过滤让代码短：直接逐日扫描）
        start = datetime.fromisoformat(first_date).date()
        end_d = datetime.fromisoformat(end).date()
        # 累计状态：按 mark 顺序回放
        cur = {c: {"shares": 0, "cost": 0.0} for c in codes}
        mark_idx = 0
        curve = []
        # 收集所有"有效交易日"（任一持仓代码当天的 K 线日期）
        all_dates = set()
        for rows in bars_by_code.values():
            for d, _ in rows:
                if start <= datetime.fromisoformat(d).date() <= end_d:
                    all_dates.add(d)
        all_dates_sorted = sorted(all_dates)
        # 找每个日期当天及之前的 marks（计算 mark_idx 累计到该日期）
        for d_str in all_dates_sorted:
            d_obj = datetime.fromisoformat(d_str).date()
            # 推进 mark_idx：处理所有 mark_date <= d_str 的
            while mark_idx < len(marks_sorted) and marks_sorted[mark_idx]["mark_date"] <= d_str:
                m = marks_sorted[mark_idx]
                item = cur[m["code"]]
                if m["side"] == "BUY":
                    current_value = item["cost"] * item["shares"]
                    added_value = m["price"] * m["shares"]
                    item["shares"] += m["shares"]
                    item["cost"] = (current_value + added_value) / item["shares"] if item["shares"] else 0.0
                else:
                    item["shares"] = max(0, item["shares"] - m["shares"])
                    if item["shares"] == 0:
                        item["cost"] = 0.0
                mark_idx += 1
            # 当日市值
            market_value = 0.0
            cost = 0.0
            for c, item in cur.items():
                if item["shares"] <= 0:
                    continue
                cost += item["cost"] * item["shares"]
                # 找该代码 d_str 的收盘价
                price = None
                for d_k, close in bars_by_code.get(c, []):
                    if d_k == d_str:
                        price = close
                        break
                if price is None:
                    # 兜底：取最近一日
                    if bars_by_code.get(c):
                        price = bars_by_code[c][-1][1]
                if price is not None:
                    market_value += item["shares"] * price
            pnl = market_value - cost
            pnl_pct = round((pnl / cost) if cost > 0 else 0, 4)
            curve.append({
                "date": d_str,
                "market_value": round(market_value, 2),
                "cost": round(cost, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": pnl_pct,
            })
        # 最后一天强制用最新价（可能 end < 最新交易日）
        if curve:
            last = curve[-1]
            last["market_value"] = round(sum(item["shares"] * self._last_price(c, bars_by_code) for c, item in cur.items() if item["shares"] > 0), 2)
            last["pnl"] = round(last["market_value"] - last["cost"], 2)
            last["pnl_pct"] = round((last["pnl"] / last["cost"]) if last["cost"] > 0 else 0, 4)
        return {
            "curve": curve,
            "summary": {
                "trade_count": len(marks_sorted),
                "first_date": first_date,
                "last_date": end,
                "final_pnl": curve[-1]["pnl"] if curve else 0.0,
                "final_pnl_pct": curve[-1]["pnl_pct"] if curve else 0.0,
            },
        }

    def _last_price(self, code: str, bars_by_code: dict) -> float:
        if not bars_by_code.get(code):
            return 0.0
        return bars_by_code[code][-1][1]

    def _next_session_plan(self) -> dict:
        """下一交易日计划：基于交易日历和今日信号候选。

        2026-06-07: 1 天 TTL(交易日历每天只算一次,信号候选当天不变)
        """
        cache_key = f"next_session:{date.today().isoformat()}"
        cached = self._ttl_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        from alphapilot.data.calendar import next_trade_day
        today = date.today()
        next_td = next_trade_day(today)
        signals = self.signals()
        candidates = []
        for s in signals:
            if s["action"] not in ("NORMAL", "TRIAL"):
                continue
            # 取最新收盘价,前端点击时可一键填入 mark 表单
            bars = self.cache.get_bars(s["code"])
            last_price = float(bars["close"].iloc[-1]) if bars is not None and len(bars) > 0 else None
            candidates.append({
                "code": s["code"], "name": s["name"],
                "action": s["action"], "score": s["score"],
                "sector": s.get("sector"),
                "last_price": round(last_price, 3) if last_price else None,
            })
            if len(candidates) >= 10:
                break
        result = {
            "next_trade_date": next_td.isoformat(),
            "days_until": (next_td - today).days,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
        self._ttl_cache.set(cache_key, result, ttl_seconds=86400.0)  # 1 天
        return result

    def backtest(self) -> dict:
        # 2026-06-07: backtest 446ms 起步,5 分钟 TTL 复用结果
        # backtest 输入不依赖实时行情,半小时内不重跑
        return self._ttl_cache.get_or_compute(
            "backtest", ttl_seconds=300.0, compute_fn=self._run_backtest
        )

    def _run_backtest(self) -> dict:
        self.ensure_initialized()
        market = self.cache.get_bars(BENCHMARKS["market"].symbol)
        sector = self.cache.get_bars(BENCHMARKS["sector"].symbol)
        leader = self.cache.get_bars("601138.SH")
        universe = {
            instrument.symbol: (instrument.name, self.cache.get_bars(instrument.symbol))
            for instrument in WATCHLIST
        }
        return Trend20Backtester().run(universe, market, sector, leader if not leader.empty else None)

    def marks(self, mode: Optional[str] = None) -> list[dict]:
        return self.journal.list_marks(mode=mode)

    def mark_trade(
        self,
        code: str,
        side: str,
        shares: int,
        price: Optional[float] = None,
        mark_date: Optional[str] = None,
        note: Optional[str] = None,
        mode: str = "real",
    ) -> dict:
        """记录一笔交易。mode='real' 真实记账，mode='paper' 模拟交易（用于策略试运行）。"""
        self.ensure_initialized()
        instrument = get_instrument(code)
        if price is None:
            bars = self.cache.get_bars(code)
            latest = _latest_bar(bars)
            if latest is None:
                raise ValueError(f"no cached price for {code}")
            price = float(latest["close"])
        # 把标的注册到 instruments（如果 get_instrument 兜底为 name=symbol）
        # 这样后续 holdings 能拿到真名而非代码
        self.cache.upsert_instrument(code, instrument.name, instrument.asset_type, instrument.sector)
        return self.journal.mark_trade(code, side, shares, float(price), mark_date=mark_date, note=note, mode=mode)

    def _loss_streak(self) -> int:
        marks = self.journal.list_marks()
        sells = [mark for mark in marks if mark["side"] == "SELL"]
        if not sells:
            return 0
        streak = 0
        for mark in sells:
            if "亏损" in (mark.get("note") or ""):
                streak += 1
            else:
                break
        return streak


def _group_signals_for_dashboard(signals: list[dict], user_picks: list[dict]) -> dict:
    """按 action + 来源分组今日信号,便于在"今日规则输出"区域分块展示。

    2026-06-07 优化: 输出精简版字段(给 dashboard 用),不再带 entry_signal / exit_signal / board
    以及下划线开头的内部字段(_user_pick / _blocked_short)直接作为普通字段输出,
    改为生成两个对前端友好的替代字段:
    - is_user_pick: bool (替代 _user_pick)
    - blocked_summary: str (替代 _blocked_short,None 时为空串)
    """
    user_codes = {p["symbol"] for p in user_picks}
    groups: dict[str, list[dict]] = {
        "buy": [],         # NORMAL 标记买入
        "trial": [],       # TRIAL 观察
        "exit_alert": [],  # EXIT_ALERT 退出提醒
        "stop": [],        # STOP 暂停
        "skip": [],        # SKIP 过滤
    }
    for s in signals:
        action = s.get("action", "SKIP")
        group_key = {
            "NORMAL": "buy",
            "TRIAL": "trial",
            "EXIT_ALERT": "exit_alert",
            "STOP": "stop",
        }.get(action, "skip")
        # 精简版: 只保留 dashboard 真正用到的字段
        blocked = s.get("blocked_reasons") or []
        groups[group_key].append({
            "code": s.get("code"),
            "name": s.get("name"),
            "sector": s.get("sector"),
            "score": s.get("score"),
            "action": action,
            "reasons": s.get("reasons") or [],
            "blocked_reasons": blocked,
            "blocked_summary": blocked[0] if blocked else "",
            "reason_text": s.get("reason_text", ""),
            "gate_state": s.get("gate_state", {}),
            "last_price": s.get("last_price"),
            "change_pct": s.get("change_pct"),
            "pnl_pct": s.get("pnl_pct"),
            "holding_shares": s.get("holding_shares", 0),
            "cost_price": s.get("cost_price"),
            "signal_type": s.get("signal_type"),
            "signal_date": s.get("signal_date"),
            "estimated_win_rate": s.get("estimated_win_rate"),
            "is_user_pick": s.get("code") in user_codes,
        })
    return groups


def _data_freshness_summary(fetch_statuses: dict) -> dict:
    """汇总 watchlist/benchmarks 的数据滞后天数。"""
    today = date.today()
    lag_distribution: dict[str, int] = {"today": 0, "1-3 天": 0, "4-7 天": 0, "8+ 天": 0, "无数据": 0}
    oldest_lag_days: Optional[int] = 0
    missing_symbols: list[str] = []
    for status in fetch_statuses.values():
        latest = status.get("latest_trade_date")
        sym = status.get("symbol")
        if not latest:
            lag_distribution["无数据"] += 1
            missing_symbols.append(sym)
            oldest_lag_days = 999
            continue
        try:
            lag = (today - date.fromisoformat(str(latest)[:10])).days
        except ValueError:
            continue
        if lag <= 0:
            lag_distribution["today"] += 1
        elif lag <= 3:
            lag_distribution["1-3 天"] += 1
        elif lag <= 7:
            lag_distribution["4-7 天"] += 1
        else:
            lag_distribution["8+ 天"] += 1
        if oldest_lag_days != 999 and (oldest_lag_days is None or lag > oldest_lag_days):
            oldest_lag_days = lag
    return {
        "lag_distribution": lag_distribution,
        "oldest_lag_days": oldest_lag_days,
        "missing_symbols": missing_symbols[:10],
        "is_stale": (oldest_lag_days or 0) >= 5,
    }


def _latest_bar(bars: pd.DataFrame):
    if bars.empty:
        return None
    return bars.iloc[-1]


def _bars_on_or_before(bars: pd.DataFrame, as_of: Optional[str]) -> pd.DataFrame:
    if as_of is None:
        return bars
    return bars[bars["trade_date"] <= as_of]


def _benchmark_above_ma20(cache: MarketDataCache, symbol: str) -> bool:
    bars = cache.get_bars(symbol)
    if len(bars) < 20:
        return False
    ma20 = bars["close"].rolling(20).mean().iloc[-1]
    return bool(bars.iloc[-1]["close"] > ma20)


def _ma(bars, window: int):
    """N 日收盘均线。数据不足返回 None。"""
    if bars is None or len(bars) < window:
        return None
    return float(bars["close"].rolling(window).mean().iloc[-1])


def _ma_slope(bars, window: int, lookback: int = 5):
    """MA(window) 在 lookback 个交易日之间的变化率。"""
    if bars is None or len(bars) < window + lookback:
        return None
    ma_series = bars["close"].rolling(window).mean()
    current = float(ma_series.iloc[-1])
    earlier = float(ma_series.iloc[-1 - lookback])
    if earlier <= 0:
        return None
    return current / earlier - 1


def _volume_ratio(bars, lookback: int = 5):
    """今日成交量 / lookback 日均量（含今日）。>1 表示放量。"""
    if bars is None or len(bars) < lookback + 1 or "volume" not in bars.columns:
        return None
    recent = bars["volume"].iloc[-(lookback + 1):]
    today = float(recent.iloc[-1])
    avg = float(recent.iloc[:-1].mean())
    if avg <= 0:
        return None
    return today / avg


def _benchmark_state(close, ma20, ma60, slope, dist_ma20) -> str:
    """基准综合状态判定（4 状态）。"""
    if close is None or ma20 is None or ma60 is None:
        return "无数据"
    # 过热优先（>MA20 5% 以上视为超买）
    if dist_ma20 is not None and dist_ma20 > 0.05:
        return "过热"
    if close > ma20 and close > ma60 and (slope is None or slope > 0):
        return "强"
    if close < ma20 and close < ma60 and (slope is None or slope < 0):
        return "弱"
    return "震荡"


def _is_pass_state(state: str) -> bool:
    """大盘过滤判定：强 + 过热 + 震荡 算"通过"（仅弱和无数据算"过滤"）。"""
    return state in {"强", "过热", "震荡"}


def _signal_board(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    if suffix == "BJ":
        return "bj"
    if suffix == "SH" and code.startswith("688"):
        return "star"
    if suffix == "SZ" and code.startswith(("300", "301")):
        return "chinext"
    return "main_board"


def _signal_board_label(symbol: str) -> str:
    return {
        "main_board": "沪深主板",
        "chinext": "创业板",
        "star": "科创板",
        "bj": "北交所",
    }.get(_signal_board(symbol), "其他")


def _parse_errors(errors_json: Optional[str]) -> list[str]:
    if not errors_json:
        return []
    try:
        parsed = json.loads(errors_json)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _is_stale(latest_trade_date: str, days: int = 3) -> bool:
    """判断数据是否过期(> days 天没更新)。用于资金流 stale 状态判断。"""
    if not latest_trade_date:
        return True
    try:
        lag = (date.today() - date.fromisoformat(str(latest_trade_date)[:10])).days
    except (ValueError, TypeError):
        return True
    return lag > days


def _settings_from_payload(payload: dict) -> Trend20Settings:
    defaults = Trend20Settings()
    values = asdict(defaults)
    field_names = {field.name for field in fields(Trend20Settings)}
    for key, value in (payload or {}).items():
        if key not in field_names:
            continue
        if isinstance(values[key], bool):
            values[key] = bool(value)
        elif isinstance(values[key], int):
            values[key] = int(value)
        elif isinstance(values[key], float):
            values[key] = float(value)

    # 边界夹逼交给 Trend20Settings.__post_init__ 处理，这里只做类型转换
    return Trend20Settings(**values)
