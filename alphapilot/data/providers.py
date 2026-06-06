from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional, Protocol
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from alphapilot.config import BENCHMARKS, BENCHMARK_CANDIDATES, WATCHLIST, Instrument
from alphapilot.data.provider_base import (
    FundFlowProvider,
    MarketDataProvider,
    make_fund_flow_provider,
    make_market_provider,
    register_fund_flow_provider,
    register_market_provider,
)
from alphapilot.data.utils import compact_error as _compact_error


def provider_by_name(name: str) -> MarketDataProvider:
    return make_market_provider(name)


def fund_flow_provider_by_name(name: str) -> FundFlowProvider:
    return make_fund_flow_provider(name)


class BulkFundFlowProvider(FundFlowProvider, Protocol):
    def fetch_all_daily(self, start_date: str, end_date: str) -> pd.DataFrame:
        ...


def normalize_akshare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "date": "trade_date",
    }
    normalized = frame.rename(columns=rename_map).copy()
    if "trade_date" not in normalized.columns:
        raise ValueError("AkShare result missing trade_date/date column")
    if "amount" not in normalized.columns:
        normalized["amount"] = normalized["close"] * normalized.get("volume", 0)
    columns = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
    normalized = normalized[columns].copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date")


@dataclass
class AkShareProvider:
    name: str = "akshare"

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("AkShare is not installed in this environment") from exc

        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        symbol = instrument.symbol.split(".")[0]

        if instrument.asset_type == "index":
            index_symbol = f"sh{symbol}" if instrument.symbol.endswith(".SH") else f"sz{symbol}"
            with _no_proxy_env():
                raw = ak.stock_zh_index_daily(symbol=index_symbol)
            frame = normalize_akshare_frame(raw)
            filtered = frame[(frame["trade_date"] >= start_date) & (frame["trade_date"] <= end_date)]
            return _with_provider(filtered, self.name)

        with _no_proxy_env():
            raw = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust=adjust_type)
        return _with_provider(normalize_akshare_frame(raw), self.name)

    def fetch_intraday_snapshot(self, instrument: Instrument) -> Optional[dict]:
        """盘中实时快照：用 stock_zh_a_spot_em 抓当前价量，合成一根临时日 K。

        返回 None 表示抓不到（环境不支持/网络失败/已收盘），上层应回退到普通 K 线。
        """
        try:
            import akshare as ak
        except ImportError:
            return None
        symbol = instrument.symbol.split(".")[0]
        try:
            with _no_proxy_env():
                spot = ak.stock_zh_a_spot_em()
        except Exception:
            return None
        # 找当前标的
        match = spot[spot["代码"].astype(str) == symbol]
        if match.empty:
            return None
        row = match.iloc[0]
        now = datetime.now()
        return {
            "trade_date": now.strftime("%Y-%m-%d"),
            "open": float(row.get("今开") or 0),
            "close": float(row.get("最新价") or 0),
            "high": float(row.get("最高") or 0),
            "low": float(row.get("最低") or 0),
            "volume": float(row.get("成交量") or 0),
            "amount": float(row.get("成交额") or 0),
        }


@dataclass
class EastMoneyCurlProvider:
    """EastMoney K-line provider using system curl.

    In this local environment Python requests/AkShare can be routed through a
    broken proxy or rejected by the remote endpoint, while curl succeeds. This
    provider keeps the same cache boundary but avoids the failing Python HTTP
    stack.
    """

    name: str = "eastmoney"
    max_retries: int = 4
    base_delay_seconds: float = 1.2
    timeout_seconds: int = 25
    connect_timeout_seconds: int = 8

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",
            "fqt": {"none": "0", "qfq": "1", "hfq": "2"}.get(adjust_type, "1"),
            "secid": _eastmoney_secid(instrument.symbol),
            "beg": start_date.replace("-", ""),
            "end": end_date.replace("-", ""),
        }
        query = urlencode(params)
        url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}"
        payload = _curl_json(
            "EastMoney",
            url,
            max_retries=self.max_retries,
            base_delay_seconds=self.base_delay_seconds,
            timeout_seconds=self.timeout_seconds,
            connect_timeout_seconds=self.connect_timeout_seconds,
        )
        if payload.get("rc") != 0 or not payload.get("data"):
            raise RuntimeError(f"EastMoney returned no data: {payload}")
        klines = payload["data"].get("klines") or []
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "trade_date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise RuntimeError(f"EastMoney returned empty K-line list for {instrument.symbol}")
        normalized = frame[["trade_date", "open", "high", "low", "close", "volume", "amount"]].sort_values("trade_date")
        return _with_provider(normalized, self.name)


@dataclass
class TencentCurlProvider:
    """Tencent K-line provider using system curl.

    This gives the MVP a second real HTTP endpoint for A-share daily bars. It
    is still a free web endpoint, so commercial use should replace it with an
    authorized data service.
    """

    name: str = "tencent"
    max_retries: int = 3
    base_delay_seconds: float = 0.8
    timeout_seconds: int = 20
    connect_timeout_seconds: int = 8
    chunk_days: int = 900
    chunk_delay_seconds: float = 0.01

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        rows = []
        for chunk_start, chunk_end in _date_chunks(start_date, end_date, chunk_days=self.chunk_days):
            rows.extend(self._fetch_rows(instrument, chunk_start, chunk_end, adjust_type=adjust_type))
            if self.chunk_delay_seconds > 0:
                time.sleep(self.chunk_delay_seconds)
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise RuntimeError(f"Tencent returned empty K-line list for {instrument.symbol}")
        frame = frame.drop_duplicates(subset=["trade_date"], keep="last")
        normalized = frame[["trade_date", "open", "high", "low", "close", "volume", "amount"]].sort_values("trade_date")
        return _with_provider(normalized, self.name)

    def _fetch_rows(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> list[dict]:
        symbol = _tencent_symbol(instrument.symbol)
        adjust = {"none": "", "qfq": "qfq", "hfq": "hfq"}.get(adjust_type, "qfq")
        limit = _tencent_limit(start_date, end_date)
        param = f"{symbol},day,{start_date},{end_date},{limit},{adjust}".rstrip(",")
        query = urlencode({"param": param})
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{query}"
        payload = _curl_json(
            "Tencent",
            url,
            max_retries=self.max_retries,
            base_delay_seconds=self.base_delay_seconds,
            timeout_seconds=self.timeout_seconds,
            connect_timeout_seconds=self.connect_timeout_seconds,
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"Tencent returned error: {payload}")
        data = (payload.get("data") or {}).get(symbol) or {}
        key = {"none": "day", "qfq": "qfqday", "hfq": "hfqday"}.get(adjust_type, "qfqday")
        klines = data.get(key) or data.get("day") or data.get("qfqday") or data.get("hfqday") or []
        rows = []
        for line in klines:
            if len(line) < 6:
                continue
            close = float(line[2])
            volume = float(line[5])
            rows.append(
                {
                    "trade_date": line[0],
                    "open": float(line[1]),
                    "close": close,
                    "high": float(line[3]),
                    "low": float(line[4]),
                    "volume": volume,
                    "amount": volume * close * 100,
                }
            )
        return rows


@dataclass
class SinaDailyProvider:
    """Sina daily K-line provider used as a fallback for BJ shares."""

    name: str = "sina"
    timeout_seconds: int = 20
    max_retries: int = 3
    base_delay_seconds: float = 0.8

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        frames = []
        errors = []
        for symbol in _sina_symbols_for_instrument(instrument):
            try:
                frame = self._fetch_symbol_daily(symbol, start_date, end_date)
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"{symbol}: {_compact_error(exc)}")

        if not frames:
            detail = "; ".join(errors) if errors else "no rows returned"
            raise RuntimeError(f"Sina returned empty K-line list for {instrument.symbol}; {detail}")
        frame = pd.concat(frames, ignore_index=True)
        frame = frame.drop_duplicates(subset=["trade_date"], keep="last")
        return _with_provider(frame[["trade_date", "open", "high", "low", "close", "volume", "amount"]].sort_values("trade_date"), self.name)

    def _fetch_symbol_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        days = max((date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 30, 800)
        query = urlencode({"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)})
        url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?{query}"
        payload = _curl_text(
            "Sina",
            url,
            max_retries=self.max_retries,
            base_delay_seconds=self.base_delay_seconds,
            timeout_seconds=self.timeout_seconds,
            connect_timeout_seconds=8,
        )
        try:
            rows = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Sina returned invalid JSON: {payload[:200]}") from exc
        output = []
        for row in rows or []:
            trade_date = str(row.get("day") or "")
            if trade_date < start_date or trade_date > end_date:
                continue
            close = float(row["close"])
            volume = float(row.get("volume") or 0)
            output.append(
                {
                    "trade_date": trade_date,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": close,
                    "volume": volume,
                    "amount": volume * close,
                }
            )
        frame = pd.DataFrame(output)
        if frame.empty:
            raise RuntimeError(f"Sina returned empty K-line list for {symbol}")
        return frame[["trade_date", "open", "high", "low", "close", "volume", "amount"]].sort_values("trade_date")


@dataclass
class EastMoneyFundFlowHistoryProvider:
    """EastMoney individual stock fund-flow provider.

    The free endpoint currently exposes recent daily history, not a guaranteed
    three-year window. The cache stores everything returned inside the requested
    date range and reports row counts per symbol.
    """

    name: str = "eastmoney_fund_flow_history"
    max_retries: int = 3
    base_delay_seconds: float = 0.8
    timeout_seconds: int = 20
    connect_timeout_seconds: int = 8

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        params = {
            "lmt": "0",
            "klt": "101",
            "secid": _eastmoney_secid(instrument.symbol),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        query = urlencode(params)
        url = f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?{query}"
        try:
            payload = _requests_json_no_proxy(
                "EastMoney fund flow",
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params=params,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            payload = _curl_json(
                "EastMoney fund flow",
                url,
                max_retries=self.max_retries,
                base_delay_seconds=self.base_delay_seconds,
                timeout_seconds=self.timeout_seconds,
                connect_timeout_seconds=self.connect_timeout_seconds,
            )
        if payload.get("rc") != 0 or not payload.get("data"):
            raise RuntimeError(f"EastMoney fund flow returned no data: {payload}")
        rows = []
        for line in payload["data"].get("klines") or []:
            parts = line.split(",")
            if len(parts) < 13:
                continue
            rows.append(
                {
                    "trade_date": parts[0],
                    "main_net": float(parts[1]),
                    "small_net": float(parts[2]),
                    "medium_net": float(parts[3]),
                    "large_net": float(parts[4]),
                    "super_large_net": float(parts[5]),
                    "main_ratio": float(parts[6]),
                    "small_ratio": float(parts[7]),
                    "medium_ratio": float(parts[8]),
                    "large_ratio": float(parts[9]),
                    "super_large_ratio": float(parts[10]),
                    "close": float(parts[11]),
                    "pct_change": float(parts[12]),
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise RuntimeError(f"EastMoney fund flow returned empty list for {instrument.symbol}")
        frame = frame[(frame["trade_date"] >= start_date) & (frame["trade_date"] <= end_date)].copy()
        if frame.empty:
            raise RuntimeError(f"EastMoney fund flow has no rows in requested range for {instrument.symbol}")
        return _with_provider(frame.sort_values("trade_date"), self.name)


@dataclass
class EastMoneyFundFlowRankProvider:
    """EastMoney market-wide fund-flow snapshot provider.

    This is the stable full-market path. It stores the latest available daily
    fund-flow fields for every matched stock in one paginated job.
    """

    name: str = "eastmoney_fund_flow"
    timeout_seconds: int = 20
    page_size: int = 100

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        frame = self.fetch_all_daily(start_date, end_date)
        matched = frame[frame["symbol"] == instrument.symbol].copy()
        if matched.empty:
            raise RuntimeError(f"EastMoney fund-flow rank returned no data for {instrument.symbol}")
        return _with_provider(matched.drop(columns=["symbol"]), self.name)

    def fetch_all_daily(self, start_date: str, end_date: str) -> pd.DataFrame:
        first = self._fetch_rank_page(page=1)
        data = first.get("data") or {}
        total = int(data.get("total") or 0)
        if total <= 0:
            raise RuntimeError(f"EastMoney fund-flow rank returned no rows: {first}")
        pages = (total + self.page_size - 1) // self.page_size
        rows = list(data.get("diff") or [])
        for page in range(2, pages + 1):
            payload = self._fetch_rank_page(page=page)
            rows.extend((payload.get("data") or {}).get("diff") or [])
            time.sleep(0.05)

        output = []
        for row in rows:
            code = str(row.get("f12") or "").zfill(6)
            name = str(row.get("f14") or "")
            if not code or _is_st_name(name):
                continue
            timestamp = int(row.get("f124") or 0)
            trade_date = date.fromtimestamp(timestamp).isoformat() if timestamp else end_date
            if trade_date < start_date or trade_date > end_date:
                continue
            output.append(
                {
                    "symbol": _a_share_symbol(code),
                    "trade_date": trade_date,
                    "close": _to_float(row.get("f2")),
                    "pct_change": _to_float(row.get("f3")),
                    "main_net": _to_float(row.get("f62")),
                    "main_ratio": _to_float(row.get("f184")),
                    "super_large_net": _to_float(row.get("f66")),
                    "super_large_ratio": _to_float(row.get("f69")),
                    "large_net": _to_float(row.get("f72")),
                    "large_ratio": _to_float(row.get("f75")),
                    "medium_net": _to_float(row.get("f78")),
                    "medium_ratio": _to_float(row.get("f81")),
                    "small_net": _to_float(row.get("f84")),
                    "small_ratio": _to_float(row.get("f87")),
                }
            )
        frame = pd.DataFrame(output)
        if frame.empty:
            raise RuntimeError("EastMoney fund-flow rank returned no usable rows")
        return _with_provider(frame.sort_values(["symbol", "trade_date"]), self.name)

    def _fetch_rank_page(self, page: int) -> dict:
        params = _fund_flow_rank_params(page=page, page_size=self.page_size)
        url = f"https://push2.eastmoney.com/api/qt/clist/get?{urlencode(params)}"
        try:
            return _requests_json_no_proxy(
                "EastMoney fund-flow rank",
                "https://push2.eastmoney.com/api/qt/clist/get",
                params=params,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as requests_exc:
            try:
                return _curl_json(
                    "EastMoney fund-flow rank",
                    url,
                    max_retries=3,
                    base_delay_seconds=1.0,
                    timeout_seconds=self.timeout_seconds,
                    connect_timeout_seconds=8,
                )
            except Exception as curl_exc:
                raise RuntimeError(
                    "EastMoney fund-flow rank page "
                    f"{page} failed; requests={_compact_error(requests_exc)}; curl={_compact_error(curl_exc)}"
                ) from curl_exc


@dataclass
class TushareMoneyflowDcProvider:
    """Tushare authorized DC stock fund-flow provider.

    It fetches all stocks by trade date, which is much more stable for a
    one-month all-A refresh than per-symbol free endpoints. Requires a
    TUSHARE_TOKEN or TS_TOKEN environment variable.
    """

    name: str = "tushare_moneyflow_dc"
    timeout_seconds: int = 30
    request_interval_seconds: float = 0.15

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        frame = self.fetch_all_daily(start_date, end_date)
        matched = frame[frame["symbol"] == instrument.symbol].copy()
        if matched.empty:
            raise RuntimeError(f"Tushare moneyflow_dc returned no data for {instrument.symbol}")
        return _with_provider(matched.drop(columns=["symbol"]), self.name)

    def fetch_all_daily(self, start_date: str, end_date: str) -> pd.DataFrame:
        token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN or TS_TOKEN is required for stable fund-flow downloads")

        frames = []
        fields = (
            "trade_date,ts_code,name,pct_change,close,net_amount,net_amount_rate,"
            "buy_elg_amount,buy_elg_amount_rate,buy_lg_amount,buy_lg_amount_rate,"
            "buy_md_amount,buy_md_amount_rate,buy_sm_amount,buy_sm_amount_rate"
        )
        for trade_day in pd.bdate_range(start=start_date, end=end_date):
            payload = _tushare_api(
                api_name="moneyflow_dc",
                token=token,
                params={"trade_date": trade_day.strftime("%Y%m%d")},
                fields=fields,
                timeout_seconds=self.timeout_seconds,
            )
            if not payload.empty:
                frames.append(payload)
            if self.request_interval_seconds > 0:
                time.sleep(self.request_interval_seconds)

        if not frames:
            raise RuntimeError("Tushare moneyflow_dc returned no rows for the requested window")
        frame = pd.concat(frames, ignore_index=True)
        output = pd.DataFrame(
            {
                "symbol": frame["ts_code"].astype(str),
                "trade_date": pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d"),
                "close": _column_or_zero(frame, "close"),
                "pct_change": _column_or_zero(frame, "pct_change"),
                "main_net": _amount_10k_to_yuan(_column_or_zero(frame, "net_amount")),
                "main_ratio": _column_or_zero(frame, "net_amount_rate"),
                "super_large_net": _amount_10k_to_yuan(_column_or_zero(frame, "buy_elg_amount")),
                "super_large_ratio": _column_or_zero(frame, "buy_elg_amount_rate"),
                "large_net": _amount_10k_to_yuan(_column_or_zero(frame, "buy_lg_amount")),
                "large_ratio": _column_or_zero(frame, "buy_lg_amount_rate"),
                "medium_net": _amount_10k_to_yuan(_column_or_zero(frame, "buy_md_amount")),
                "medium_ratio": _column_or_zero(frame, "buy_md_amount_rate"),
                "small_net": _amount_10k_to_yuan(_column_or_zero(frame, "buy_sm_amount")),
                "small_ratio": _column_or_zero(frame, "buy_sm_amount_rate"),
            }
        )
        for column in [column for column in output.columns if column not in {"symbol", "trade_date"}]:
            output[column] = pd.to_numeric(output[column], errors="coerce").fillna(0.0)
        output = output.dropna(subset=["trade_date"])
        if output.empty:
            raise RuntimeError("Tushare moneyflow_dc returned no usable rows after normalization")
        return _with_provider(output.sort_values(["symbol", "trade_date"]), self.name)


@dataclass
class FallbackProvider:
    """Provider chain for production-like local downloads.

    It never falls back to synthetic sample data, so live-data failures stay
    visible instead of silently producing trade-facing demo signals.
    """

    name: str = "auto"
    providers: list[MarketDataProvider] = field(default_factory=list)
    bj_providers: list[MarketDataProvider] = field(default_factory=list)

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        errors = []
        providers = self.bj_providers if instrument.symbol.endswith(".BJ") and self.bj_providers else self.providers
        for provider in providers:
            try:
                frame = provider.fetch_daily(instrument, start_date, end_date, adjust_type=adjust_type)
                return _with_provider(frame, frame.attrs.get("provider", provider.name))
            except Exception as exc:
                errors.append(f"{provider.name}: {_compact_error(exc)}")
        raise RuntimeError(f"{self.name} provider failed for {instrument.symbol}; " + "; ".join(errors))


@dataclass
class SampleDataProvider:
    """Deterministic local data provider used for tests and offline MVP demos."""

    name: str = "sample"

    def fetch_daily(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ) -> pd.DataFrame:
        dates = pd.bdate_range(start=start_date, end=end_date)
        if len(dates) < 80:
            raise ValueError("sample provider needs at least 80 business days")

        seed = int(hashlib.sha256(instrument.symbol.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        n = len(dates)
        base = 20 + (seed % 9000) / 180
        if instrument.asset_type == "index":
            base = 800 + (seed % 5000) / 2
        elif instrument.asset_type == "etf":
            base = 0.8 + (seed % 1000) / 1000

        drift = {
            "000001.SH": 0.08,
            "000300.SH": 0.05,
            "000905.SH": 0.12,
            "399006.SZ": -0.03,
            "000688.SH": 0.20,
            "000016.SH": 0.02,
            "159770.SZ": 0.18,
            "159995.SZ": 0.25,
            "159992.SZ": -0.05,
            "300124.SZ": 0.22,
            "002230.SZ": 0.14,
            "601138.SH": 0.26,
            "688256.SH": 0.38,
            "002415.SZ": -0.02,
            "002475.SZ": 0.12,
            "300014.SZ": -0.04,
        }.get(instrument.symbol, 0.06)
        volatility = 0.012 if instrument.asset_type != "index" else 0.006
        returns = rng.normal(drift / 252, volatility, n)
        seasonal = np.sin(np.linspace(0, 18, n)) * 0.003
        close = base * np.exp(np.cumsum(returns + seasonal))

        # Shape the most recent window so the demo reliably shows mixed outputs.
        if instrument.symbol == "000001.SH":
            close[-30:] = np.linspace(close[-31] * 0.98, close[-31] * 1.06, 30)
        elif instrument.symbol == "159770.SZ":
            close[-30:] = np.linspace(close[-31] * 0.97, close[-31] * 1.14, 30)
        elif instrument.symbol == "300124.SZ":
            close[-30:] = np.linspace(close[-31] * 0.96, close[-31] * 1.08, 30)
        elif instrument.symbol == "002230.SZ":
            close[-25:] = np.concatenate(
                [np.linspace(close[-26] * 0.92, close[-26] * 0.96, 12), np.linspace(close[-26] * 0.97, close[-26] * 1.07, 13)]
            )
        elif instrument.symbol == "601138.SH":
            close[-30:] = np.linspace(close[-31] * 0.95, close[-31] * 1.20, 30)
        elif instrument.symbol == "688256.SH":
            close[-20:] = np.linspace(close[-21] * 1.05, close[-21] * 1.36, 20)
        elif instrument.symbol == "002415.SZ":
            close[-30:] = np.linspace(close[-31] * 0.98, close[-31] * 0.91, 30)

        open_ = close * (1 + rng.normal(0, volatility / 3, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.006, 0.004, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.006, 0.004, n)))
        volume = rng.integers(200_000, 6_000_000, n).astype(float)
        amount = volume * close

        frame = pd.DataFrame(
            {
                "trade_date": dates.strftime("%Y-%m-%d"),
                "open": np.round(open_, 3),
                "high": np.round(high, 3),
                "low": np.round(low, 3),
                "close": np.round(close, 3),
                "volume": volume,
                "amount": np.round(amount, 2),
            }
        )
        return _with_provider(frame, self.name)


def universe_by_name(name: str) -> list[Instrument]:
    if name in {"sample", "watchlist"}:
        return [*BENCHMARKS.values(), *WATCHLIST]
    if name == "benchmarks":
        # 所有候选基准（含 3 个默认 + 槽位候选），方便一次性拉齐数据
        seen: set[str] = set()
        out: list[Instrument] = []
        for inst in [*BENCHMARKS.values(), *BENCHMARK_CANDIDATES["market"], *BENCHMARK_CANDIDATES["style"], *BENCHMARK_CANDIDATES["sector"]]:
            if inst.symbol not in seen:
                seen.add(inst.symbol)
                out.append(inst)
        return out
    if name == "all_a":
        return discover_all_a_stocks(include_st=False)
    raise ValueError(f"unsupported universe: {name}")


def discover_all_a_stocks(include_st: bool = False) -> list[Instrument]:
    errors = []
    for discovery in (_discover_all_a_stocks_eastmoney, _discover_all_a_stocks_akshare):
        try:
            instruments = discovery(include_st=include_st)
            if len(instruments) >= 1000:
                return instruments
            errors.append(f"{discovery.__name__}: incomplete universe with {len(instruments)} rows")
        except Exception as exc:
            errors.append(f"{discovery.__name__}: {_compact_error(exc)}")
    raise RuntimeError("unable to discover A-share universe; " + "; ".join(errors))


def _discover_all_a_stocks_eastmoney(include_st: bool = False) -> list[Instrument]:
    params = {
        "pn": "1",
        "pz": "10000",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f13,f14",
    }
    query = urlencode(params)
    errors = []
    payload = None
    for host in ("push2.eastmoney.com", "82.push2.eastmoney.com"):
        url = f"https://{host}/api/qt/clist/get?{query}"
        try:
            payload = _curl_json(
                "EastMoney stock list",
                url,
                max_retries=3,
                base_delay_seconds=1.0,
                timeout_seconds=30,
                connect_timeout_seconds=8,
            )
            break
        except Exception as exc:
            errors.append(f"{host}: {_compact_error(exc)}")
    if payload is None:
        raise RuntimeError("; ".join(errors))
    rows = (payload.get("data") or {}).get("diff") or []
    return _instruments_from_code_name_rows(
        [{"code": row.get("f12"), "name": row.get("f14")} for row in rows],
        include_st=include_st,
    )


def _discover_all_a_stocks_akshare(include_st: bool = False) -> list[Instrument]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AkShare is not installed in this environment") from exc

    with _no_proxy_env():
        frame = ak.stock_info_a_code_name()
    return _instruments_from_code_name_rows(frame[["code", "name"]].to_dict("records"), include_st=include_st)


def _instruments_from_code_name_rows(rows: list[dict], include_st: bool = False) -> list[Instrument]:
    instruments = []
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        if not include_st and _is_st_name(name):
            continue
        instruments.append(Instrument(symbol=_a_share_symbol(code), name=name, asset_type="stock", sector="A股"))
    return sorted(instruments, key=lambda item: item.symbol)


def default_start_date(years: int = 3, today: Optional[date] = None) -> str:
    today = today or date.today()
    return (today - timedelta(days=365 * years + 10)).isoformat()


def _eastmoney_secid(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    market = "1" if suffix == "SH" else "0"
    return f"{market}.{code}"


def _tencent_symbol(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    if suffix == "SH":
        market = "sh"
    elif suffix == "BJ":
        market = "bj"
    else:
        market = "sz"
    return f"{market}{code}"


def _sina_symbol(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    if suffix == "SH":
        market = "sh"
    elif suffix == "BJ":
        market = "bj"
    else:
        market = "sz"
    return f"{market}{code}"


def _sina_symbols_for_instrument(instrument: Instrument) -> list[str]:
    symbols = [_sina_symbol(instrument.symbol)]
    code, _, suffix = instrument.symbol.partition(".")
    alias = BSE_920_TO_OLD_CODE.get(code) if suffix == "BJ" else None
    if alias:
        symbols.append(f"bj{alias}")
    return symbols


def _a_share_symbol(code: str) -> str:
    if code.startswith(("600", "601", "603", "605", "688")):
        suffix = "SH"
    elif code.startswith(("4", "8", "92")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def _is_st_name(name: str) -> bool:
    normalized = name.upper()
    return "ST" in normalized


def _fund_flow_rank_params(page: int, page_size: int) -> dict:
    return {
        "fid": "f62",
        "po": "1",
        "pz": str(page_size),
        "pn": str(page),
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,"
        "m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f124",
    }


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _amount_10k_to_yuan(values) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").fillna(0.0) * 10000


def _column_or_zero(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(0.0, index=frame.index)


@contextmanager
def _no_proxy_env():
    keys = ["NO_PROXY", "no_proxy"]
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = "*"
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _tencent_limit(start_date: str, end_date: str) -> int:
    days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 10
    return min(max(days, 800), 5000)


def _date_chunks(start_date: str, end_date: str, chunk_days: int) -> list[tuple[str, str]]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)
    return chunks


def _curl_json(
    label: str,
    url: str,
    max_retries: int,
    base_delay_seconds: float,
    timeout_seconds: int,
    connect_timeout_seconds: int,
) -> dict:
    last_error = "unknown error"
    for attempt in range(1, max_retries + 1):
        result = subprocess.run(
            [
                "curl",
                "-4",
                "-sS",
                "--connect-timeout",
                str(connect_timeout_seconds),
                "--max-time",
                str(timeout_seconds),
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        if result.returncode == 0 and stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON: {exc}; body={stdout[:200]}"
        else:
            last_error = result.stderr.strip() or stdout[:200] or f"curl exited with {result.returncode}"

        if attempt < max_retries:
            delay = base_delay_seconds * attempt + random.uniform(0.0, 0.6)
            time.sleep(delay)
    raise RuntimeError(f"{label} request failed after {max_retries} attempts: {last_error}")


def _curl_text(
    label: str,
    url: str,
    max_retries: int,
    base_delay_seconds: float,
    timeout_seconds: int,
    connect_timeout_seconds: int,
) -> str:
    last_error = "unknown error"
    for attempt in range(1, max_retries + 1):
        result = subprocess.run(
            [
                "curl",
                "-4",
                "-sS",
                "--connect-timeout",
                str(connect_timeout_seconds),
                "--max-time",
                str(timeout_seconds),
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        if result.returncode == 0 and stdout:
            return stdout
        last_error = result.stderr.strip() or stdout[:200] or f"curl exited with {result.returncode}"
        if attempt < max_retries:
            delay = base_delay_seconds * attempt + random.uniform(0.0, 0.6)
            time.sleep(delay)
    raise RuntimeError(f"{label} request failed after {max_retries} attempts: {last_error}")


def _requests_json_no_proxy(label: str, url: str, params: dict, timeout_seconds: int) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is not installed in this environment") from exc

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/",
    }
    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            with _no_proxy_env():
                response = requests.get(url, params=params, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            return response.json()
        except ValueError as exc:
            last_error = f"invalid JSON: {exc}"
        except Exception as exc:
            last_error = _compact_error(exc)
        if attempt < 3:
            time.sleep(attempt + random.uniform(0.0, 0.5))
    raise RuntimeError(f"{label} request failed after 3 attempts: {last_error}")


def _tushare_api(api_name: str, token: str, params: dict, fields: str, timeout_seconds: int) -> pd.DataFrame:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is not installed in this environment") from exc

    body = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }
    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            with _no_proxy_env():
                response = requests.post("http://api.tushare.pro", json=body, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("code") or 0) != 0:
                raise RuntimeError(str(payload.get("msg") or payload))
            data = payload.get("data") or {}
            items = data.get("items") or []
            columns = data.get("fields") or []
            return pd.DataFrame(items, columns=columns)
        except Exception as exc:
            last_error = _compact_error(exc)
        if attempt < 3:
            time.sleep(attempt + random.uniform(0.0, 0.5))
    raise RuntimeError(f"Tushare {api_name} request failed after 3 attempts: {last_error}")


def _with_provider(frame: pd.DataFrame, provider: str) -> pd.DataFrame:
    frame = frame.copy()
    frame.attrs["provider"] = provider
    return frame


# Northbound BSE 920 code migration aliases. Some free K-line endpoints only
# retain deep history under the pre-migration NEEQ/BSE code, so the downloader
# queries both symbols and stores the result under the current 920 code.
BSE_920_TO_OLD_CODE = {
    "920508": "835508",
    "920509": "833509",
    "920510": "430510",
    "920522": "832522",
    "920523": "833523",
    "920526": "831526",
    "920527": "873527",
    "920533": "833533",
    "920541": "872541",
    "920547": "836547",
    "920553": "871553",
    "920556": "430556",
    "920564": "430564",
    "920566": "832566",
    "920570": "873570",
    "920571": "833171",
    "920576": "873576",
    "920578": "871478",
    "920579": "835579",
    "920580": "833580",
}


# ----- 在所有类定义之后再注册到 provider_base 注册表 ------------------------


# 注册内置行情数据源
register_market_provider("sample", SampleDataProvider)
register_market_provider(
    "auto",
    lambda: FallbackProvider(
        providers=[TencentCurlProvider(), SinaDailyProvider(), EastMoneyCurlProvider(), AkShareProvider()],
        bj_providers=[SinaDailyProvider(), TencentCurlProvider(), EastMoneyCurlProvider(), AkShareProvider()],
    ),
)
register_market_provider("tencent", TencentCurlProvider)
register_market_provider("sina", SinaDailyProvider)
register_market_provider("eastmoney", EastMoneyCurlProvider)
register_market_provider("akshare", AkShareProvider)

# 注册内置资金流数据源
register_fund_flow_provider("eastmoney", EastMoneyFundFlowRankProvider)
register_fund_flow_provider("eastmoney_fund_flow", EastMoneyFundFlowRankProvider)
register_fund_flow_provider("eastmoney_fund_flow_rank", EastMoneyFundFlowRankProvider)
register_fund_flow_provider("eastmoney_fund_flow_history", EastMoneyFundFlowHistoryProvider)
register_fund_flow_provider("eastmoney_history", EastMoneyFundFlowHistoryProvider)
register_fund_flow_provider("tushare", TushareMoneyflowDcProvider)
register_fund_flow_provider("tushare_moneyflow_dc", TushareMoneyflowDcProvider)
