from __future__ import annotations

from typing import Callable, Optional, Protocol


class MarketDataProvider(Protocol):
    """K 线数据源协议（任何实现 fetch_daily 协议的类都是 provider）。"""

    name: str

    def fetch_daily(
        self,
        instrument,
        start_date: str,
        end_date: str,
        adjust_type: str = "qfq",
    ):
        ...


class FundFlowProvider(Protocol):
    """资金流数据源协议。"""

    name: str

    def fetch_daily(
        self,
        instrument,
        start_date: str,
        end_date: str,
    ):
        ...


# 工厂注册表：name -> 创建函数
_MARKET_FACTORIES: dict[str, Callable[[], MarketDataProvider]] = {}
_FUND_FLOW_FACTORIES: dict[str, Callable[[], FundFlowProvider]] = {}


def register_market_provider(name: str, factory: Callable[[], MarketDataProvider]) -> None:
    """注册一个新的行情数据源工厂。"""
    if not name or not isinstance(name, str):
        raise ValueError("provider name must be a non-empty string")
    _MARKET_FACTORIES[name] = factory


def register_fund_flow_provider(name: str, factory: Callable[[], FundFlowProvider]) -> None:
    """注册一个新的资金流数据源工厂。"""
    if not name or not isinstance(name, str):
        raise ValueError("provider name must be a non-empty string")
    _FUND_FLOW_FACTORIES[name] = factory


def make_market_provider(name: str) -> MarketDataProvider:
    if name not in _MARKET_FACTORIES:
        raise ValueError(
            f"unsupported provider: {name}; available: {sorted(_MARKET_FACTORIES)}"
        )
    return _MARKET_FACTORIES[name]()


def make_fund_flow_provider(name: str) -> FundFlowProvider:
    if name not in _FUND_FLOW_FACTORIES:
        raise ValueError(
            f"unsupported provider: {name}; available: {sorted(_FUND_FLOW_FACTORIES)}"
        )
    return _FUND_FLOW_FACTORIES[name]()


def list_market_providers() -> list[str]:
    return sorted(_MARKET_FACTORIES)


def list_fund_flow_providers() -> list[str]:
    return sorted(_FUND_FLOW_FACTORIES)
