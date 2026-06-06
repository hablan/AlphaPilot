from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
