from __future__ import annotations

import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

import pandas as pd

from alphapilot.data.cache import MarketDataCache
from alphapilot.data.calendar import is_market_open_on
from alphapilot.data.providers import BSE_920_TO_OLD_CODE, default_start_date, fund_flow_provider_by_name, provider_by_name, universe_by_name
from alphapilot.data.utils import compact_error as _compact_error


# A 股盘前集合竞价 09:15-09:25 连续竞价 09:30-11:30 13:00-15:00
MARKET_OPEN_TIME = dtime(9, 30)
MARKET_CLOSE_TIME = dtime(15, 0)
LUNCH_START = dtime(11, 30)
LUNCH_END = dtime(13, 0)

# K 线最低行数门槛：少于这个数量的标的视为 "未补齐"，强制重新拉取
# 避免 sample 残留的 1 行 / 极短片段被误判为 recent，导致 launchd 永远跳过
MIN_BARS_FOR_RECENT = 60  # ≈ 1 季度的交易日


def is_market_open(now: Optional[datetime] = None) -> bool:
    """判断当前是否在 A 股交易时段内。

    委托给 calendar.is_market_open_on 处理节假日（2025-2026 已知调休 + 节日）。
    """
    now = now or datetime.now()
    return is_market_open_on(now.date(), now.strftime("%H:%M"))


def next_market_open(now: Optional[datetime] = None) -> datetime:
    """下次开市时刻（用于在盘后/节假日时计算增量起点）。"""
    now = now or datetime.now()
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < candidate:
        return candidate
    candidate = (now + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _apply_intraday_override(provider, instrument, bars: pd.DataFrame, end_date: str) -> bool:
    """盘中时用实时快照覆盖当日 K 线。

    当且仅当：今天是交易日且市场在开盘时段、provider 实现 fetch_intraday_snapshot、
    且 bars 中最后一行就是今天（end_date），才尝试覆盖。
    返回 True 表示覆盖成功。
    """
    if not is_market_open():
        return False
    snapshot_fn = getattr(provider, "fetch_intraday_snapshot", None)
    if snapshot_fn is None:
        return False
    snap = snapshot_fn(instrument)
    if not snap:
        return False
    # bars 的最后一行是今天才覆盖
    if bars.empty or str(bars["trade_date"].iloc[-1]) != end_date:
        return False
    last = bars.iloc[-1].copy()
    for key in ("open", "close", "high", "low", "volume", "amount"):
        if snap.get(key) is not None and snap[key] > 0:
            last[key] = snap[key]
    bars.iloc[-1] = last
    return True


def initialize_market_cache(
    cache: MarketDataCache,
    provider_name: str = "sample",
    universe_name: str = "watchlist",
    years: int = 3,
    end_date: Optional[str] = None,
    adjust_type: str = "qfq",
    request_interval_seconds: Optional[float] = None,
    resume: bool = False,
    incomplete_only: bool = False,
    incremental: bool = False,
    max_symbols: Optional[int] = None,
) -> dict:
    """初始化（或增量更新）K 线缓存。

    - full (默认): 拉全量 years 年窗口
    - resume=True: 跳过最近 10 天已覆盖的标的
    - incomplete_only=True: 跳过所有有 K 线的标的
    - incremental=True: 起点 = last_trade_date+1（首次拉取走 full 窗口），适合日常增量更新
    """
    provider = provider_by_name(provider_name)
    instruments = universe_by_name(universe_name)
    if max_symbols is not None:
        instruments = instruments[:max_symbols]
    end = end_date or date.today().isoformat()
    start_default = default_start_date(years)
    run_id = cache.start_fetch_run(provider.name, universe_name, start_default, end)
    interval = request_interval_seconds if request_interval_seconds is not None else _default_interval(provider.name)
    success = 0
    skipped = 0
    refreshed = 0  # 增量模式下覆盖了最新一日
    failures: list[str] = []

    skip_cached = resume or incomplete_only or incremental
    for index, instrument in enumerate(instruments):
        attempted_request = False
        try:
            # 决定该标的的拉取窗口
            latest_in_db = cache.latest_trade_date(instrument.symbol, adjust_type=adjust_type)
            if incremental and latest_in_db:
                # 增量：只拉 last_trade_date+1 到 today
                fetch_start = _plus_days(str(latest_in_db), 1)
                if fetch_start > end:
                    # 已覆盖到 end，跳过
                    summary = cache.bar_summary(instrument.symbol, adjust_type=adjust_type)
                    cache.upsert_instrument(instrument.symbol, instrument.name, instrument.asset_type, instrument.sector)
                    cache.record_symbol_fetch_status(
                        instrument.symbol,
                        instrument.name,
                        str(summary.get("provider") or "cache"),
                        "SKIPPED",
                        summary.get("latest_trade_date"),
                        int(summary.get("row_count") or 0),
                        f"incremental up-to-date (latest={latest_in_db})",
                    )
                    skipped += 1
                    continue
            else:
                fetch_start = start_default

            if skip_cached and fetch_start == start_default and _has_recent_bars(cache, instrument.symbol, fetch_start, end, adjust_type=adjust_type):
                summary = cache.bar_summary(instrument.symbol, adjust_type=adjust_type)
                cache.upsert_instrument(instrument.symbol, instrument.name, instrument.asset_type, instrument.sector)
                cache.record_symbol_fetch_status(
                    instrument.symbol,
                    instrument.name,
                    str(summary.get("provider") or "cache"),
                    "SKIPPED",
                    summary.get("latest_trade_date"),
                    int(summary.get("row_count") or 0),
                    "cached data already covers the requested window",
                )
                skipped += 1
                continue

            attempted_request = True
            bars = provider.fetch_daily(instrument, fetch_start, end, adjust_type=adjust_type)
            _validate_bars(instrument.symbol, bars)
            # 当日 K 线补丁：盘中使用实时快照覆盖当日 K 线（如果 provider 支持）
            intraday_overridden = _apply_intraday_override(provider, instrument, bars, end)
            source_provider = str(bars.attrs.get("provider") or provider.name)
            cache.upsert_instrument(instrument.symbol, instrument.name, instrument.asset_type, instrument.sector)
            before_count = int(cache.bar_summary(instrument.symbol, adjust_type=adjust_type).get("row_count") or 0)
            inserted = cache.upsert_bars(
                instrument.symbol,
                bars,
                provider=source_provider,
                adjust_type=adjust_type,
                data_version=f"{source_provider}:{fetch_start}:{end}:{adjust_type}{':intraday' if intraday_overridden else ''}",
            )
            after_count = int(cache.bar_summary(instrument.symbol, adjust_type=adjust_type).get("row_count") or 0)
            # 是否覆盖了已存在的同日 K 线（=盘中/盘后重复拉取）
            if incremental and latest_in_db and inserted > 0 and after_count <= before_count:
                refreshed += 1
            cache.record_symbol_fetch_status(
                instrument.symbol,
                instrument.name,
                source_provider,
                "SUCCESS",
                str(bars["trade_date"].max()),
                len(bars),
            )
            success += 1
        except Exception as exc:  # pragma: no cover - exercised by real provider failures.
            message = _compact_error(exc)
            failures.append(f"{instrument.symbol} {instrument.name}: {message}")
            cache.record_symbol_fetch_status(
                instrument.symbol,
                instrument.name,
                provider.name,
                "FAILED",
                cache.latest_trade_date(instrument.symbol),
                0,
                message,
            )
        finally:
            if attempted_request and interval > 0 and index < len(instruments) - 1:
                time.sleep(interval)

    if not failures:
        status = "SUCCESS"
    elif success + skipped == 0:
        status = "FAILED"
    else:
        status = "PARTIAL"
    cache.finish_fetch_run(run_id, status, success + skipped, len(failures), failures)
    market_status = "OPEN" if is_market_open() else "CLOSED"
    # 2026-06-07: 返回 as_of + new_bar_count,前端可以不强制 reload 直接更新 banner
    fresh_status = cache.lightweight_cache_status()
    return {
        "provider": provider.name,
        "universe": universe_name,
        "mode": "incremental" if incremental else "full",
        "start_date": start_default,
        "end_date": end,
        "as_of": date.today().isoformat(),  # 2026-06-07 加
        "new_bar_count": fresh_status.get("bar_count", 0),  # 2026-06-07 加
        "new_latest_trade_date": fresh_status.get("latest_trade_date"),  # 2026-06-07 加
        "market_status": market_status,
        "success_count": success,
        "skipped_count": skipped,
        "refreshed_count": refreshed,
        "failure_count": len(failures),
        "errors": failures,
    }


def initialize_fund_flow_cache(
    cache: MarketDataCache,
    provider_name: str = "eastmoney_fund_flow",
    universe_name: str = "watchlist",
    years: int = 3,
    days: Optional[int] = None,
    end_date: Optional[str] = None,
    request_interval_seconds: Optional[float] = None,
    resume: bool = False,
    incomplete_only: bool = False,
    max_symbols: Optional[int] = None,
) -> dict:
    provider = fund_flow_provider_by_name(provider_name)
    instruments = [item for item in universe_by_name(universe_name) if item.asset_type == "stock"]
    if max_symbols is not None:
        instruments = instruments[:max_symbols]
    end = end_date or date.today().isoformat()
    if days is not None:
        start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    else:
        start = default_start_date(years, today=date.fromisoformat(end))
    run_id = cache.start_fetch_run(provider.name, universe_name, start, end)
    interval = request_interval_seconds if request_interval_seconds is not None else 0.2
    success = 0
    skipped = 0
    failures: list[str] = []
    bulk_flows = None
    has_bulk_provider = hasattr(provider, "fetch_all_daily")

    skip_cached = resume or incomplete_only
    pending_instruments = []
    for instrument in instruments:
        if skip_cached and _has_recent_fund_flow(cache, instrument.symbol, end):
            summary = cache.fund_flow_summary(instrument.symbol)
            cache.record_fund_flow_fetch_status(
                instrument.symbol,
                instrument.name,
                str(summary.get("provider") or "cache"),
                "SKIPPED",
                summary.get("latest_trade_date"),
                int(summary.get("row_count") or 0),
                "cached fund-flow data is recent",
            )
            skipped += 1
        else:
            pending_instruments.append(instrument)

    if has_bulk_provider and pending_instruments:
        try:
            bulk_flows = provider.fetch_all_daily(start, end)
        except Exception as exc:  # pragma: no cover - exercised by live provider failures.
            message = _compact_error(exc)
            failures.append(f"{provider.name} bulk fund-flow fetch failed: {message}")
            for instrument in pending_instruments:
                cache.record_fund_flow_fetch_status(
                    instrument.symbol,
                    instrument.name,
                    provider.name,
                    "FAILED",
                    cache.fund_flow_summary(instrument.symbol).get("latest_trade_date"),
                    0,
                    message,
                )
            status = "FAILED" if skipped == 0 else "PARTIAL"
            cache.finish_fetch_run(run_id, status, skipped, len(pending_instruments), failures)
            return {
                "provider": provider.name,
                "universe": universe_name,
                "start_date": start,
                "end_date": end,
                "success_count": 0,
                "skipped_count": skipped,
                "failure_count": len(pending_instruments),
                "errors": failures,
            }

    for index, instrument in enumerate(pending_instruments):
        attempted_request = False
        try:
            if bulk_flows is not None:
                flows = bulk_flows[bulk_flows["symbol"] == instrument.symbol].copy()
                if "symbol" in flows.columns:
                    flows = flows.drop(columns=["symbol"])
                if flows.empty:
                    raise RuntimeError("no fund-flow row returned by bulk provider")
                flows.attrs["provider"] = getattr(provider, "name", provider_name)
            else:
                attempted_request = True
                flows = provider.fetch_daily(instrument, start, end)
            _validate_fund_flows(instrument.symbol, flows)
            source_provider = str(flows.attrs.get("provider") or provider.name)
            cache.upsert_instrument(instrument.symbol, instrument.name, instrument.asset_type, instrument.sector)
            cache.upsert_fund_flows(
                instrument.symbol,
                flows,
                provider=source_provider,
                data_version=f"{source_provider}:{start}:{end}",
            )
            cache.record_fund_flow_fetch_status(
                instrument.symbol,
                instrument.name,
                source_provider,
                "SUCCESS",
                str(flows["trade_date"].max()),
                len(flows),
            )
            success += 1
        except Exception as exc:  # pragma: no cover - exercised by live provider failures.
            message = _compact_error(exc)
            failures.append(f"{instrument.symbol} {instrument.name}: {message}")
            cache.record_fund_flow_fetch_status(
                instrument.symbol,
                instrument.name,
                provider.name,
                "FAILED",
                cache.fund_flow_summary(instrument.symbol).get("latest_trade_date"),
                0,
                message,
            )
        finally:
            if attempted_request and interval > 0 and index < len(pending_instruments) - 1:
                time.sleep(interval)

    if not failures:
        status = "SUCCESS"
    elif success + skipped == 0:
        status = "FAILED"
    else:
        status = "PARTIAL"
    cache.finish_fetch_run(run_id, status, success + skipped, len(failures), failures)
    return {
        "provider": provider.name,
        "universe": universe_name,
        "start_date": start,
        "end_date": end,
        "market_status": "OPEN" if is_market_open() else "CLOSED",
        "success_count": success,
        "skipped_count": skipped,
        "failure_count": len(failures),
        "errors": failures,
    }


def _default_interval(provider_name: str) -> float:
    return 0.0 if provider_name == "sample" else 1.2


def _validate_bars(symbol: str, bars: pd.DataFrame) -> None:
    if bars.empty:
        raise ValueError(f"{symbol} returned empty K-line data")
    required = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"{symbol} K-line data missing columns: {missing}")
    if bars["trade_date"].duplicated().any():
        raise ValueError(f"{symbol} K-line data has duplicate trade dates")
    numeric = bars[["open", "high", "low", "close", "volume", "amount"]].apply(pd.to_numeric, errors="coerce")
    if numeric[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{symbol} K-line data has invalid OHLC values")
    if (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol} K-line data has non-positive OHLC values")
    if ((numeric["high"] < numeric[["open", "close"]].max(axis=1)) | (numeric["low"] > numeric[["open", "close"]].min(axis=1))).any():
        raise ValueError(f"{symbol} K-line data has inconsistent high/low values")


def _validate_fund_flows(symbol: str, flows: pd.DataFrame) -> None:
    if flows.empty:
        raise ValueError(f"{symbol} returned empty fund-flow data")
    required = [
        "trade_date",
        "close",
        "pct_change",
        "main_net",
        "main_ratio",
        "super_large_net",
        "super_large_ratio",
        "large_net",
        "large_ratio",
        "medium_net",
        "medium_ratio",
        "small_net",
        "small_ratio",
    ]
    missing = [column for column in required if column not in flows.columns]
    if missing:
        raise ValueError(f"{symbol} fund-flow data missing columns: {missing}")
    if flows["trade_date"].duplicated().any():
        raise ValueError(f"{symbol} fund-flow data has duplicate trade dates")
    numeric = flows[[column for column in required if column != "trade_date"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError(f"{symbol} fund-flow data has invalid numeric values")


def _has_recent_bars(cache: MarketDataCache, symbol: str, start_date: str, end_date: str, adjust_type: str = "qfq") -> bool:
    summary = cache.bar_summary(symbol, adjust_type=adjust_type)
    latest = summary.get("latest_trade_date")
    row_count = int(summary.get("row_count") or 0)
    if not latest:
        return False
    # 最低行数门槛：避免 1 行 sample 残留被误判为 "recent"，导致 launchd / --incomplete-only 永远跳过该标的
    if row_count < MIN_BARS_FOR_RECENT:
        return False
    if symbol.endswith(".BJ") and row_count <= 10:
        code = symbol.split(".")[0]
        if code in BSE_920_TO_OLD_CODE:
            return False
        first = summary.get("first_trade_date")
        return bool(first and _date_is_recent(str(first), end_date, max_lag_days=30) and _date_is_recent(str(latest), end_date))
    return bool(_date_is_recent(str(latest), end_date) and row_count > 0)


def _has_recent_fund_flow(cache: MarketDataCache, symbol: str, end_date: str) -> bool:
    summary = cache.fund_flow_summary(symbol)
    latest = summary.get("latest_trade_date")
    row_count = int(summary.get("row_count") or 0)
    return bool(latest and _date_is_recent(str(latest), end_date) and row_count > 0)


def _date_is_recent(trade_date: str, end_date: str, max_lag_days: int = 10) -> bool:
    return date.fromisoformat(trade_date) >= date.fromisoformat(end_date) - timedelta(days=max_lag_days)


def _plus_days(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
