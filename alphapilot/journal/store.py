from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from alphapilot.config import get_instrument
from alphapilot.data.cache import MarketDataCache


class JournalStore:
    def __init__(self, cache: MarketDataCache):
        self.cache = cache

    def mark_trade(
        self,
        code: str,
        side: str,
        shares: int,
        price: float,
        mark_date: Optional[str] = None,
        note: Optional[str] = None,
        source_signal_id: Optional[str] = None,
        mode: str = "real",
    ) -> dict:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if mode not in {"real", "paper"}:
            raise ValueError("mode must be 'real' or 'paper'")
        if shares <= 0:
            raise ValueError("shares must be positive")
        if price <= 0:
            raise ValueError("price must be positive")

        instrument = get_instrument(code)
        # 兜底：get_instrument 找不到时 name=code，从 DB 或 signal 接口查真名
        if instrument.name == code:
            with self.cache.connect() as conn:
                row = conn.execute(
                    "select name from instruments where symbol = ? limit 1", (code,)
                ).fetchone()
            if row and row["name"]:
                instrument = type(instrument)(symbol=code, name=row["name"], asset_type=instrument.asset_type, sector=instrument.sector)
        mark_date = mark_date or date.today().isoformat()
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self.cache.connect() as conn:
            cur = conn.execute(
                """
                insert into trade_marks(code, name, side, shares, price, mark_date, source_signal_id, note, mode, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (code, instrument.name, side, shares, float(price), mark_date, source_signal_id, note, mode, now),
            )
            mark_id = int(cur.lastrowid)
        return {
            "id": mark_id,
            "code": code,
            "name": instrument.name,
            "side": side,
            "shares": shares,
            "price": price,
            "mark_date": mark_date,
            "note": note,
            "mode": mode,
        }

    def list_marks(self, mode: Optional[str] = None) -> list[dict]:
        """列出 mark。mode=None 返回所有；mode='real'/'paper' 只返回该模式。"""
        with self.cache.connect() as conn:
            if mode is None:
                rows = conn.execute(
                    """
                    select id, code, name, side, shares, price, mark_date, source_signal_id, note, mode, created_at
                      from trade_marks
                     order by mark_date desc, id desc
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select id, code, name, side, shares, price, mark_date, source_signal_id, note, mode, created_at
                      from trade_marks
                     where mode = ?
                     order by mark_date desc, id desc
                    """,
                    (mode,),
                ).fetchall()
        return [dict(row) for row in rows]

    def holdings(self, mode: Optional[str] = None) -> dict[str, dict]:
        """计算持仓：按 mark 顺序还原 BUY/SELL 累计。

        mode=None 时聚合 real+paper（兼容老调用）。
        'real'/'paper' 时只算该模式。
        """
        holdings: dict[str, dict] = {}
        for mark in reversed(self.list_marks(mode=mode)):
            item = holdings.setdefault(mark["code"], {"code": mark["code"], "name": mark["name"], "shares": 0, "cost": 0.0})
            if mark["side"] == "BUY":
                current_value = item["cost"] * item["shares"]
                added_value = mark["price"] * mark["shares"]
                item["shares"] += mark["shares"]
                item["cost"] = (current_value + added_value) / item["shares"] if item["shares"] else 0.0
            else:
                item["shares"] = max(0, item["shares"] - mark["shares"])
                if item["shares"] == 0:
                    item["cost"] = 0.0
        return holdings
