from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from alphapilot.config import DEFAULT_DB_PATH, ensure_data_dir


class MarketDataCache:
    """SQLite-backed MVP market data cache.

    The product plan can later swap this for DuckDB + Parquet without changing
    strategy code because reads and writes go through this boundary.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        ensure_data_dir()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                create table if not exists instruments (
                    symbol text primary key,
                    name text not null,
                    asset_type text not null,
                    sector text not null,
                    updated_at text not null
                );

                create table if not exists daily_bars (
                    symbol text not null,
                    trade_date text not null,
                    open real not null,
                    high real not null,
                    low real not null,
                    close real not null,
                    volume real not null,
                    amount real not null,
                    frequency text not null default '1d',
                    adjust_type text not null default 'qfq',
                    provider text not null,
                    data_version text not null,
                    fetched_at text not null,
                    primary key (symbol, trade_date, frequency, adjust_type, provider)
                );

                create index if not exists idx_daily_bars_symbol_date
                    on daily_bars(symbol, trade_date);

                create index if not exists idx_daily_bars_symbol_fetched
                    on daily_bars(symbol, fetched_at);

                create table if not exists fetch_runs (
                    id integer primary key autoincrement,
                    started_at text not null,
                    ended_at text,
                    provider text not null,
                    universe text not null,
                    start_date text not null,
                    end_date text not null,
                    status text not null,
                    success_count integer not null default 0,
                    failure_count integer not null default 0,
                    errors_json text not null default '[]'
                );

                create table if not exists symbol_fetch_status (
                    symbol text primary key,
                    name text not null,
                    provider text not null,
                    status text not null,
                    latest_trade_date text,
                    row_count integer not null default 0,
                    message text,
                    attempted_at text not null
                );

                create table if not exists fund_flow_daily (
                    symbol text not null,
                    trade_date text not null,
                    close real not null,
                    pct_change real not null,
                    main_net real not null,
                    main_ratio real not null,
                    super_large_net real not null,
                    super_large_ratio real not null,
                    large_net real not null,
                    large_ratio real not null,
                    medium_net real not null,
                    medium_ratio real not null,
                    small_net real not null,
                    small_ratio real not null,
                    provider text not null,
                    data_version text not null,
                    fetched_at text not null,
                    primary key (symbol, trade_date, provider)
                );

                create index if not exists idx_fund_flow_symbol_date
                    on fund_flow_daily(symbol, trade_date);

                create table if not exists fund_flow_fetch_status (
                    symbol text primary key,
                    name text not null,
                    provider text not null,
                    status text not null,
                    latest_trade_date text,
                    row_count integer not null default 0,
                    message text,
                    attempted_at text not null
                );

                create table if not exists trade_marks (
                    id integer primary key autoincrement,
                    code text not null,
                    name text not null,
                    side text not null,
                    shares integer not null,
                    price real not null,
                    mark_date text not null,
                    source_signal_id text,
                    note text,
                    mode text not null default 'real',  -- 'real' = 真实记账；'paper' = 模拟交易
                    created_at text not null
                );
                create index if not exists idx_trade_marks_mode
                    on trade_marks(mode, code, mark_date);

                create table if not exists app_settings (
                    key text primary key,
                    value_json text not null,
                    updated_at text not null
                );

                create table if not exists watchlist (
                    symbol text primary key,
                    name text not null,
                    asset_type text not null default 'stock',
                    sector text not null default '',
                    added_at text not null
                );
                create index if not exists idx_watchlist_added
                    on watchlist(added_at);
                """
            )
            # === 兼容老库迁移 ===
            # 老 trade_marks 表没有 mode 列，add column 失败时忽略
            try:
                conn.execute("alter table trade_marks add column mode text not null default 'real'")
            except sqlite3.OperationalError:
                pass  # 已存在
            try:
                conn.execute("create index if not exists idx_trade_marks_mode on trade_marks(mode, code, mark_date)")
            except sqlite3.OperationalError:
                pass

    def start_fetch_run(self, provider: str, universe: str, start_date: str, end_date: str) -> int:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            cur = conn.execute(
                """
                insert into fetch_runs(started_at, provider, universe, start_date, end_date, status)
                values (?, ?, ?, ?, ?, 'RUNNING')
                """,
                (now, provider, universe, start_date, end_date),
            )
            return int(cur.lastrowid)

    def finish_fetch_run(
        self,
        run_id: int,
        status: str,
        success_count: int,
        failure_count: int,
        errors: Iterable[str],
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                update fetch_runs
                   set ended_at = ?, status = ?, success_count = ?, failure_count = ?, errors_json = ?
                 where id = ?
                """,
                (now, status, success_count, failure_count, json.dumps(list(errors), ensure_ascii=False), run_id),
            )

    def get_setting(self, key: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("select value_json from app_settings where key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def set_setting(self, key: str, value: dict) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                insert into app_settings(key, value_json, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True), now),
            )

    def delete_setting(self, key: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("delete from app_settings where key = ?", (key,))
        return cur.rowcount > 0

    def upsert_instrument(self, symbol: str, name: str, asset_type: str, sector: str) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                insert into instruments(symbol, name, asset_type, sector, updated_at)
                values (?, ?, ?, ?, ?)
                on conflict(symbol) do update set
                    name = excluded.name,
                    asset_type = excluded.asset_type,
                    sector = excluded.sector,
                    updated_at = excluded.updated_at
                """,
                (symbol, name, asset_type, sector, now),
            )

    def upsert_bars(
        self,
        symbol: str,
        bars: pd.DataFrame,
        provider: str,
        adjust_type: str = "qfq",
        frequency: str = "1d",
        data_version: Optional[str] = None,
    ) -> int:
        if bars.empty:
            return 0

        required = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
        missing = [column for column in required if column not in bars.columns]
        if missing:
            raise ValueError(f"bars missing required columns: {missing}")

        fetched_at = datetime.utcnow().isoformat(timespec="microseconds")
        version = data_version or fetched_at
        rows = []
        for row in bars[required].itertuples(index=False):
            rows.append(
                (
                    symbol,
                    str(row.trade_date),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    float(row.amount),
                    frequency,
                    adjust_type,
                    provider,
                    version,
                    fetched_at,
                )
            )

        with self.connect() as conn:
            conn.executemany(
                """
                insert into daily_bars(
                    symbol, trade_date, open, high, low, close, volume, amount,
                    frequency, adjust_type, provider, data_version, fetched_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(symbol, trade_date, frequency, adjust_type, provider) do update set
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    data_version = excluded.data_version,
                    fetched_at = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def upsert_fund_flows(
        self,
        symbol: str,
        flows: pd.DataFrame,
        provider: str,
        data_version: Optional[str] = None,
    ) -> int:
        if flows.empty:
            return 0

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
            raise ValueError(f"fund flows missing required columns: {missing}")

        fetched_at = datetime.utcnow().isoformat(timespec="microseconds")
        version = data_version or fetched_at
        rows = []
        for row in flows[required].itertuples(index=False):
            rows.append(
                (
                    symbol,
                    str(row.trade_date),
                    float(row.close),
                    float(row.pct_change),
                    float(row.main_net),
                    float(row.main_ratio),
                    float(row.super_large_net),
                    float(row.super_large_ratio),
                    float(row.large_net),
                    float(row.large_ratio),
                    float(row.medium_net),
                    float(row.medium_ratio),
                    float(row.small_net),
                    float(row.small_ratio),
                    provider,
                    version,
                    fetched_at,
                )
            )

        with self.connect() as conn:
            conn.executemany(
                """
                insert into fund_flow_daily(
                    symbol, trade_date, close, pct_change, main_net, main_ratio,
                    super_large_net, super_large_ratio, large_net, large_ratio,
                    medium_net, medium_ratio, small_net, small_ratio,
                    provider, data_version, fetched_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(symbol, trade_date, provider) do update set
                    close = excluded.close,
                    pct_change = excluded.pct_change,
                    main_net = excluded.main_net,
                    main_ratio = excluded.main_ratio,
                    super_large_net = excluded.super_large_net,
                    super_large_ratio = excluded.super_large_ratio,
                    large_net = excluded.large_net,
                    large_ratio = excluded.large_ratio,
                    medium_net = excluded.medium_net,
                    medium_ratio = excluded.medium_ratio,
                    small_net = excluded.small_net,
                    small_ratio = excluded.small_ratio,
                    data_version = excluded.data_version,
                    fetched_at = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def get_bars(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust_type: str = "qfq",
        provider: Optional[str] = None,
    ) -> pd.DataFrame:
        provider = provider or self.latest_provider(symbol, adjust_type)
        clauses = ["symbol = ?", "adjust_type = ?"]
        params = [symbol, adjust_type]
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(end_date)
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        where = " and ".join(clauses)
        with self.connect() as conn:
            frame = pd.read_sql_query(
                f"""
                select symbol, trade_date, open, high, low, close, volume, amount,
                       frequency, adjust_type, provider, data_version, fetched_at
                  from daily_bars
                 where {where}
                 order by trade_date
                """,
                conn,
                params=params,
            )
        return frame

    def get_bars_many(
        self,
        symbols: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust_type: str = "qfq",
        limit_rows: Optional[int] = None,
    ) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        # 增量更新后同一 symbol 的不同 trade_date 可能由不同 fetched_at 拉取。
        # 因此 latest 应当按 (symbol, trade_date) 取每个日期最新一次抓取，
        # 而不是对整个 symbol 取一个 max(fetched_at)（那样会丢掉历史日期的更新）。
        sql = f"""
            with latest as (
                select symbol, trade_date, max(fetched_at) as fetched_at
                  from daily_bars
                 where symbol in ({placeholders}) and adjust_type = ?
                   and (? is null or trade_date >= ?)
                   and (? is null or trade_date <= ?)
                 group by symbol, trade_date
            ),
            ranked as (
                select b.symbol, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.amount,
                       b.frequency, b.adjust_type, b.provider, b.data_version, b.fetched_at,
                       row_number() over (
                           partition by b.symbol
                           order by b.trade_date desc
                       ) as rn
                  from daily_bars b
                  join latest l
                    on b.symbol = l.symbol
                   and b.trade_date = l.trade_date
                   and b.fetched_at = l.fetched_at
                 where b.symbol in ({placeholders}) and b.adjust_type = ?
                   and (? is null or b.trade_date >= ?)
                   and (? is null or b.trade_date <= ?)
            )
            select symbol, trade_date, open, high, low, close, volume, amount,
                   frequency, adjust_type, provider, data_version, fetched_at
              from ranked
             {('where rn <= ?' if limit_rows is not None else '')}
             order by symbol, trade_date
        """
        params = [
            *symbols, adjust_type, start_date, start_date, end_date, end_date,
            *symbols, adjust_type, start_date, start_date, end_date, end_date,
        ]
        if limit_rows is not None:
            params.append(int(limit_rows))
        with self.connect() as conn:
            frame = pd.read_sql_query(sql, conn, params=params)
        if frame.empty:
            return {}
        return {symbol: group.reset_index(drop=True) for symbol, group in frame.groupby("symbol", sort=False)}

    def latest_provider(self, symbol: str, adjust_type: str = "qfq") -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                """
                select provider
                  from daily_bars
                 where symbol = ? and adjust_type = ?
                 group by provider
                 order by max(fetched_at) desc
                 limit 1
                """,
                (symbol, adjust_type),
            ).fetchone()
        return row["provider"] if row else None

    def latest_trade_date(self, symbol: str, adjust_type: str = "qfq") -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                """
                select max(trade_date) as latest
                  from daily_bars
                 where symbol = ? and adjust_type = ?
                """,
                (symbol, adjust_type),
            ).fetchone()
        return row["latest"] if row and row["latest"] else None

    def bar_summary(self, symbol: str, adjust_type: str = "qfq") -> dict:
        provider = self.latest_provider(symbol, adjust_type)
        clauses = ["symbol = ?", "adjust_type = ?"]
        params = [symbol, adjust_type]
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        where = " and ".join(clauses)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                select count(*) as row_count, min(trade_date) as first_trade_date, max(trade_date) as latest_trade_date
                  from daily_bars
                 where {where}
                """,
                params,
            ).fetchone()
        return {
            "provider": provider,
            "row_count": int(row["row_count"] or 0) if row else 0,
            "first_trade_date": row["first_trade_date"] if row else None,
            "latest_trade_date": row["latest_trade_date"] if row else None,
        }

    def fund_flow_summary(self, symbol: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                select provider, count(*) as row_count, min(trade_date) as first_trade_date, max(trade_date) as latest_trade_date
                  from fund_flow_daily
                 where symbol = ?
                 group by provider
                 order by max(fetched_at) desc
                 limit 1
                """,
                (symbol,),
            ).fetchone()
        return {
            "provider": row["provider"] if row else None,
            "row_count": int(row["row_count"] or 0) if row else 0,
            "first_trade_date": row["first_trade_date"] if row else None,
            "latest_trade_date": row["latest_trade_date"] if row else None,
        }

    def record_symbol_fetch_status(
        self,
        symbol: str,
        name: str,
        provider: str,
        status: str,
        latest_trade_date: Optional[str],
        row_count: int,
        message: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                insert into symbol_fetch_status(
                    symbol, name, provider, status, latest_trade_date, row_count, message, attempted_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(symbol) do update set
                    name = excluded.name,
                    provider = excluded.provider,
                    status = excluded.status,
                    latest_trade_date = excluded.latest_trade_date,
                    row_count = excluded.row_count,
                    message = excluded.message,
                    attempted_at = excluded.attempted_at
                """,
                (symbol, name, provider, status, latest_trade_date, int(row_count), message, now),
            )

    def fetch_symbol_statuses(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select symbol, name, provider, status, latest_trade_date, row_count, message, attempted_at
                  from symbol_fetch_status
                 order by symbol
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_fund_flow_fetch_status(
        self,
        symbol: str,
        name: str,
        provider: str,
        status: str,
        latest_trade_date: Optional[str],
        row_count: int,
        message: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                insert into fund_flow_fetch_status(
                    symbol, name, provider, status, latest_trade_date, row_count, message, attempted_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(symbol) do update set
                    name = excluded.name,
                    provider = excluded.provider,
                    status = excluded.status,
                    latest_trade_date = excluded.latest_trade_date,
                    row_count = excluded.row_count,
                    message = excluded.message,
                    attempted_at = excluded.attempted_at
                """,
                (symbol, name, provider, status, latest_trade_date, int(row_count), message, now),
            )

    def fetch_fund_flow_statuses(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select symbol, name, provider, status, latest_trade_date, row_count, message, attempted_at
                  from fund_flow_fetch_status
                 order by symbol
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_instruments(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "select symbol, name, asset_type, sector, updated_at from instruments order by symbol"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_watchlist(self) -> list[dict]:
        """读取用户自选股（含代码级 watchlist + 内置 WATCHLIST 去重）。"""
        with self.connect() as conn:
            rows = conn.execute(
                "select symbol, name, asset_type, sector, added_at from watchlist order by added_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_to_watchlist(self, symbol: str, name: str, asset_type: str = "stock", sector: str = "") -> bool:
        """加入自选股，已存在则跳过。返回是否新增。"""
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.connect() as conn:
            cur = conn.execute("select 1 from watchlist where symbol=?", (symbol,)).fetchone()
            if cur:
                return False
            conn.execute(
                "insert into watchlist(symbol, name, asset_type, sector, added_at) values (?, ?, ?, ?, ?)",
                (symbol, name, asset_type, sector, now),
            )
        return True

    def remove_from_watchlist(self, symbol: str) -> bool:
        """从自选股删除。返回是否删除了。"""
        with self.connect() as conn:
            cur = conn.execute("delete from watchlist where symbol=?", (symbol,))
            return cur.rowcount > 0

    def cache_status(self) -> dict:
        with self.connect() as conn:
            bar_count = conn.execute("select count(*) as count from daily_bars").fetchone()["count"]
            symbol_count = conn.execute("select count(distinct symbol) as count from daily_bars").fetchone()["count"]
            latest = conn.execute("select max(trade_date) as latest from daily_bars").fetchone()["latest"]
            fund_flow_count = conn.execute("select count(*) as count from fund_flow_daily").fetchone()["count"]
            fund_flow_symbol_count = conn.execute(
                "select count(distinct symbol) as count from fund_flow_daily"
            ).fetchone()["count"]
            fund_flow_latest = conn.execute(
                "select max(trade_date) as latest from fund_flow_daily"
            ).fetchone()["latest"]
            run = conn.execute(
                """
                select * from fetch_runs
                 where provider in ('sample', 'auto', 'tencent', 'sina', 'eastmoney', 'akshare')
                 order by id desc
                 limit 1
                """
            ).fetchone()
            fund_flow_run = conn.execute(
                """
                select * from fetch_runs
                 where provider in ('eastmoney_fund_flow', 'eastmoney_fund_flow_history', 'tushare_moneyflow_dc')
                 order by id desc
                 limit 1
                """
            ).fetchone()
        return {
            "db_path": str(self.db_path),
            "bar_count": bar_count,
            "symbol_count": symbol_count,
            "latest_trade_date": latest,
            "fund_flow_count": fund_flow_count,
            "fund_flow_symbol_count": fund_flow_symbol_count,
            "fund_flow_latest_trade_date": fund_flow_latest,
            "last_fetch_run": dict(run) if run else None,
            "last_fund_flow_fetch_run": dict(fund_flow_run) if fund_flow_run else None,
        }

    def lightweight_cache_status(self) -> dict:
        # 重要：bar_count 必须与 cache_status() 一致（统一口径）
        # 之前从 symbol_fetch_status 聚合 row_count 会被 upsert 跳过，
        # 永远滞后于 daily_bars 实际行数。现在直接 count(*)。
        with self.connect() as conn:
            bars = conn.execute(
                "select count(*) as bar_count, count(distinct symbol) as symbol_count, "
                "max(trade_date) as latest from daily_bars"
            ).fetchone()
            instrument_count = conn.execute("select count(*) as count from instruments").fetchone()["count"]
            fund_flow_count = conn.execute("select count(*) as count from fund_flow_daily").fetchone()["count"]
            fund_flow_symbol_count = conn.execute(
                "select count(distinct symbol) as count from fund_flow_daily"
            ).fetchone()["count"]
            fund_flow_latest = conn.execute(
                "select max(trade_date) as latest from fund_flow_daily"
            ).fetchone()["latest"]
            run = conn.execute(
                """
                select * from fetch_runs
                 where provider in ('sample', 'auto', 'tencent', 'sina', 'eastmoney', 'akshare')
                 order by id desc
                 limit 1
                """
            ).fetchone()
            fund_flow_run = conn.execute(
                """
                select * from fetch_runs
                 where provider in ('eastmoney_fund_flow', 'eastmoney_fund_flow_history', 'tushare_moneyflow_dc')
                 order by id desc
                 limit 1
                """
            ).fetchone()
        symbol_count = int(bars["symbol_count"] or 0) if bars else 0
        return {
            "db_path": str(self.db_path),
            "bar_count": int(bars["bar_count"] or 0) if bars else 0,
            "symbol_count": symbol_count or int(instrument_count or 0),
            "latest_trade_date": bars["latest"] if bars and bars["latest"] else None,
            "fund_flow_count": fund_flow_count,
            "fund_flow_symbol_count": fund_flow_symbol_count,
            "fund_flow_latest_trade_date": fund_flow_latest,
            "last_fetch_run": dict(run) if run else None,
            "last_fund_flow_fetch_run": dict(fund_flow_run) if fund_flow_run else None,
        }

    def has_bars(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("select 1 from daily_bars limit 1").fetchone()
        return row is not None
