from __future__ import annotations

import argparse
import json
import os
import sys


# 真实数据源白名单：sample 仅在 ALLOW_SAMPLE_DATA=1 时允许
ALLOWED_PROVIDERS = ["auto", "tencent", "sina", "eastmoney", "akshare"]


def _ensure_real_provider(provider: str, action: str) -> None:
    """非 sample 才能跑 init-data/refresh。

    通过 ALLOW_SAMPLE_DATA=1 显式开启样本模式（仅用于离线 demo / 测试）。
    """
    if provider == "sample" and os.environ.get("ALLOW_SAMPLE_DATA") != "1":
        print(
            f"ERROR: provider=sample 在生产路径中已被禁用。\n"
            f"  命令：alphapilot {action} --provider {provider} ...\n"
            f"  说明：sample 是确定性假数据，仅用于离线演示和单元测试；\n"
            f"        真实决策请使用 auto / tencent / sina / eastmoney / akshare 之一。\n"
            f"  解封：export ALLOW_SAMPLE_DATA=1 再运行（仅用于 demo / 测试）。",
            file=sys.stderr,
        )
        sys.exit(2)

from alphapilot.config import SERVER_HOST, SERVER_PORT
from alphapilot.server import run_server
from alphapilot.service import AlphaPilotService


def main() -> None:
    parser = argparse.ArgumentParser(prog="alphapilot", description="AlphaPilot Trend20 MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    init_data = sub.add_parser("init-data", help="initialize local K-line cache")
    init_data.add_argument("--provider", default="auto", choices=ALLOWED_PROVIDERS + ["sample"],
                           help="real data source required in production; sample only with ALLOW_SAMPLE_DATA=1")
    init_data.add_argument("--universe", default="watchlist", choices=["benchmarks", "watchlist", "sample", "all_a"])
    init_data.add_argument("--years", default=3, type=int)
    init_data.add_argument("--sleep", default=None, type=float, help="seconds to wait between live provider requests")
    init_data.add_argument("--resume", action="store_true", help="skip symbols with recent complete local K-line cache")
    init_data.add_argument("--incomplete-only", action="store_true", help="only fetch symbols missing a recent complete local K-line cache")
    init_data.add_argument("--incremental", action="store_true", help="增量更新：每个标的只拉 last_trade_date+1 到今天；盘中使用实时快照覆盖当日 K 线")
    init_data.add_argument("--max-symbols", default=None, type=int, help="limit symbol count for smoke tests")
    init_data.add_argument("--include-fund-flow", action="store_true", help="also cache available daily stock fund-flow data")

    init_flow = sub.add_parser("init-fund-flow", help="initialize local stock fund-flow cache")
    init_flow.add_argument(
        "--provider",
        default="eastmoney_fund_flow",
        choices=[
            "eastmoney",
            "eastmoney_fund_flow",
            "eastmoney_fund_flow_rank",
            "eastmoney_fund_flow_history",
            "tushare",
            "tushare_moneyflow_dc",
        ],
    )
    init_flow.add_argument("--universe", default="watchlist", choices=["watchlist", "sample", "all_a"])
    init_flow.add_argument("--years", default=3, type=int)
    init_flow.add_argument("--days", default=None, type=int, help="download only the latest N calendar days")
    init_flow.add_argument("--sleep", default=None, type=float, help="seconds to wait between live provider requests")
    init_flow.add_argument("--resume", action="store_true", help="skip symbols with recent local fund-flow cache")
    init_flow.add_argument("--incomplete-only", action="store_true", help="only fetch symbols missing recent local fund-flow data")
    init_flow.add_argument("--max-symbols", default=None, type=int, help="limit symbol count for smoke tests")

    sub.add_parser("status", help="show local cache status")
    sub.add_parser("signals", help="generate latest Trend20 signals")
    sub.add_parser("backtest", help="run Trend20 backtest on cached data")

    # 由 launchd 每日 15:30 调度的增量更新（plumb --include-fund-flow 走资金流同步）
    refresh = sub.add_parser("refresh", help="incremental update: K-line and (optional) fund-flow")
    refresh.add_argument("--provider", default="auto", choices=ALLOWED_PROVIDERS + ["sample"],
                        help="real data source required in production; sample only with ALLOW_SAMPLE_DATA=1")
    refresh.add_argument("--universe", default="watchlist", choices=["benchmarks", "watchlist", "sample", "all_a"])
    refresh.add_argument("--sleep", default=None, type=float, help="seconds to wait between live provider requests")
    refresh.add_argument("--resume", action="store_true", help="also skip fund-flow rows that are recent")
    refresh.add_argument("--include-fund-flow", action="store_true", help="also cache daily stock fund-flow data")

    mark = sub.add_parser("mark", help="mark a manual buy/sell action")
    mark.add_argument("code")
    mark.add_argument("side", choices=["BUY", "SELL"])
    mark.add_argument("--shares", type=int, required=True)
    mark.add_argument("--price", type=float)
    mark.add_argument("--note")

    serve = sub.add_parser("serve", help="run local MVP web server")
    serve.add_argument("--host", default=SERVER_HOST)
    serve.add_argument("--port", default=SERVER_PORT, type=int)

    args = parser.parse_args()
    service = AlphaPilotService()

    # 拒 sample：保证生产路径不用 demo 数据
    if args.command in ("init-data", "refresh"):
        _ensure_real_provider(args.provider, args.command)

    if args.command == "init-data":
        result = {
            "kline": service.initialize_data(
                provider=args.provider,
                universe=args.universe,
                years=args.years,
                request_interval_seconds=args.sleep,
                resume=args.resume,
                incomplete_only=args.incomplete_only,
                incremental=args.incremental,
                max_symbols=args.max_symbols,
            )
        }
        if args.include_fund_flow:
            result["fund_flow"] = service.initialize_fund_flow_data(
                universe=args.universe,
                years=args.years,
                request_interval_seconds=args.sleep,
                resume=args.resume,
                incomplete_only=args.incomplete_only,
                max_symbols=args.max_symbols,
            )
        print_json(result)
    elif args.command == "init-fund-flow":
        print_json(
            service.initialize_fund_flow_data(
                provider=args.provider,
                universe=args.universe,
                years=args.years,
                days=args.days,
                request_interval_seconds=args.sleep,
                resume=args.resume,
                incomplete_only=args.incomplete_only,
                max_symbols=args.max_symbols,
            )
        )
    elif args.command == "refresh":
        # 由 launchd 每日 15:30 调度；非交易日会跑空，service 已对节假日做兜底
        from datetime import date
        from alphapilot.data.bootstrap import is_market_open
        result = {
            "triggered_at": date.today().isoformat(),
            "market_status": "OPEN" if is_market_open() else "CLOSED",
            "kline": service.incremental_update(
                provider=args.provider,
                universe=args.universe,
                request_interval_seconds=args.sleep,
            ),
        }
        if args.include_fund_flow:
            result["fund_flow"] = service.initialize_fund_flow_data(
                universe=args.universe,
                resume=args.resume,
                request_interval_seconds=args.sleep,
            )
        print_json(result)
    elif args.command == "status":
        print_json(service.cache_status())
    elif args.command == "signals":
        print_json(service.signals())
    elif args.command == "backtest":
        print_json(service.backtest())
    elif args.command == "mark":
        print_json(service.mark_trade(args.code, args.side, args.shares, price=args.price, note=args.note))
    elif args.command == "serve":
        run_server(args.host, args.port)


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
