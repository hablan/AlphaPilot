"""A 股交易日历。

简单方案：硬编码 2025-2026 节假日 + 调休 + 周末。
判断 is_trade_day(date) -> bool。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional


# 已知休市日（2025-2026 节假日 + 调休 + 临时休市日）
# 数据来源：交易所公告 + akshare tool_trade_date_hist_sina
KNOWN_HOLIDAYS: set[str] = {
    # 2025 年
    "2025-01-01",  # 元旦
    "2025-01-28", "2025-01-29",  # 春节
    "2025-01-30", "2025-01-31",  # 春节
    "2025-02-03", "2025-02-04",  # 春节调休补休
    "2025-02-05", "2025-02-06", "2025-02-07",  # 春节
    "2025-04-04", "2025-04-05", "2025-04-06",  # 清明
    "2025-05-01", "2025-05-02", "2025-05-03",  # 劳动节
    "2025-05-05",  # 劳动节调休补休
    "2025-05-31", "2025-06-01", "2025-06-02",  # 端午
    "2025-10-01", "2025-10-02", "2025-10-03",  # 国庆
    "2025-10-04", "2025-10-05", "2025-10-06",  # 国庆
    "2025-10-07", "2025-10-08",  # 国庆
    "2025-10-10",  # 国庆调休补休
    # 2026 年
    "2026-01-01", "2026-01-02",  # 元旦
    "2026-02-17", "2026-02-18", "2026-02-19",  # 春节
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",  # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",  # 清明
    "2026-05-01", "2026-05-02", "2026-05-03",  # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",  # 端午
    "2026-09-25", "2026-09-26", "2026-09-27",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-03",  # 国庆
    "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆
}


# 调休补班：周末但开市
KNOWN_MAKEUP_DAYS: set[str] = {
    # 2025 年
    "2025-01-26",  # 周日补春节
    "2025-02-08",  # 周六补春节
    "2025-04-27",  # 周日补五一
    "2025-09-28",  # 周日补国庆
    "2025-10-11",  # 周六补国庆
    # 2026 年
    "2026-01-04",  # 周日补元旦
    "2026-02-14",  # 周六补春节
    "2026-02-28",  # 周六补春节
    "2026-05-09",  # 周六补五一
    "2026-09-27",  # 周日补中秋
    "2026-10-10",  # 周六补国庆
}


def is_trade_day(d: date) -> bool:
    """判断是否 A 股交易日。"""
    s = d.isoformat()
    if d.weekday() >= 5:  # 周六周日
        return s in KNOWN_MAKEUP_DAYS
    # 工作日：不在节假日里就是交易日
    return s not in KNOWN_HOLIDAYS


def trade_days_between(start: date, end: date) -> list[date]:
    """返回闭区间 [start, end] 内的所有交易日。"""
    if end < start:
        return []
    return [d for d in _daterange(start, end) if is_trade_day(d)]


def next_trade_day(d: date) -> date:
    """返回 d 之后的最近一个交易日。d 本身若开市则返回 d。"""
    cur = d
    while not is_trade_day(cur):
        cur = _add_day(cur, 1)
    return cur


def previous_trade_day(d: date) -> date:
    """返回 d 之前的最近一个交易日。"""
    cur = d
    while not is_trade_day(cur):
        cur = _add_day(cur, -1)
    return cur


def last_n_trade_days(d: date, n: int) -> list[date]:
    """返回截至 d 的最近 n 个交易日（含 d 若是交易日）。"""
    out: list[date] = []
    cur = d
    while len(out) < n:
        if is_trade_day(cur):
            out.append(cur)
        cur = _add_day(cur, -1)
    return list(reversed(out))


def _daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur = _add_day(cur, 1)


def _add_day(d: date, days: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=days)


def is_market_open_on(d: date, time_str: Optional[str] = None) -> bool:
    """判断某个具体日期 + 时间点是否在 A 股交易时段。

    工作日 9:30-11:30 / 13:00-15:00。
    """
    from datetime import datetime, time as dtime
    if not is_trade_day(d):
        return False
    if time_str is None:
        time_str = datetime.now().strftime("%H:%M")
    h, m = time_str.split(":")
    t = dtime(int(h), int(m))
    if t < dtime(9, 30) or t >= dtime(15, 0):
        return False
    if dtime(11, 30) <= t < dtime(13, 0):
        return False
    return True
