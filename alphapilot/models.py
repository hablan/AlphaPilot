from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# 2026-06-07: dashboard 用到的 Signal 精简版字段(白名单,集中在这里)
# 老 signals() 返回 22 字段,dashboard 不需要 entry_signal/exit_signal/board
# 这里用 frozenset 集中管理,方便扩展 + 防止遗忘
DASHBOARD_SIGNAL_FIELDS: frozenset[str] = frozenset({
    "code", "name", "sector", "score", "action", "reasons",
    "blocked_reasons", "blocked_summary", "reason_text",
    "gate_state", "last_price", "change_pct", "pnl_pct",
    "holding_shares", "cost_price", "signal_type", "signal_date",
    "estimated_win_rate", "is_user_pick",
})


def to_dashboard_signal(s: dict, is_user_pick: bool = False) -> dict:
    """2026-06-07: 统一从全量 signal dict 提取 dashboard 字段。

    之前 `_group_signals_for_dashboard` 在 service.py 里手写字段白名单,
    容易漏字段/不同步。改到这里后:
    - 模型层知道 dashboard 要哪些字段
    - service 层只负责 group + 调用此函数
    - 前端不会看到下划线字段(生成 dict 时已规范化)
    """
    blocked = s.get("blocked_reasons") or []
    return {
        "code": s.get("code"),
        "name": s.get("name"),
        "sector": s.get("sector"),
        "score": s.get("score"),
        "action": s.get("action"),
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
        "is_user_pick": is_user_pick,
    }


@dataclass
class Signal:
    code: str
    name: str
    signal_date: str
    signal_type: str
    action: str
    score: int
    estimated_win_rate: float
    reasons: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    gate_state: Dict[str, Any] = field(default_factory=dict)
    entry_signal: Dict[str, Any] = field(default_factory=dict)
    exit_signal: Dict[str, Any] = field(default_factory=dict)
    pnl_pct: Optional[float] = None
    holding_shares: int = 0
    cost_price: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        """从全量 signal dict 提取核心字段建 dataclass(忽略 entry/exit/board/last_price/change_pct 等)。"""
        return cls(
            code=d.get("code", ""),
            name=d.get("name", ""),
            signal_date=d.get("signal_date", ""),
            signal_type=d.get("signal_type", ""),
            action=d.get("action", "SKIP"),
            score=d.get("score", 0),
            estimated_win_rate=d.get("estimated_win_rate", 0.0),
            reasons=d.get("reasons") or [],
            blocked_reasons=d.get("blocked_reasons") or [],
            gate_state=d.get("gate_state") or {},
            pnl_pct=d.get("pnl_pct"),
            holding_shares=d.get("holding_shares", 0),
            cost_price=d.get("cost_price"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "signal_date": self.signal_date,
            "signal_type": self.signal_type,
            "action": self.action,
            "score": self.score,
            "estimated_win_rate": self.estimated_win_rate,
            "reasons": self.reasons,
            "blocked_reasons": self.blocked_reasons,
            "gate_state": self.gate_state,
            "entry_signal": self.entry_signal,
            "exit_signal": self.exit_signal,
            "pnl_pct": self.pnl_pct,
            "holding_shares": self.holding_shares,
            "cost_price": self.cost_price,
        }

    def to_dashboard_dict(self, is_user_pick: bool = False) -> dict:
        """Signal 自身的精简版,直接用全字段。"""
        return to_dashboard_signal(self.to_dict(), is_user_pick=is_user_pick)


@dataclass
class Trade:
    code: str
    name: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    action: str
    shares: int
    pnl_pct: float
    exit_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

