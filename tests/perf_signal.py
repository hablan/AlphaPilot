"""AlphaPilot 信号生成 + 回测性能基准。

跑两次（一次不命中缓存模拟旧实现，一次命中 IndicatorCache 模拟新实现），
对比 wall-clock 耗时。

运行::

    cd /Users/henry/Applications/AlphaPilot
    python3 tests/perf_signal.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 确保能 import alphapilot
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alphapilot.service import AlphaPilotService  # noqa: E402


def time_it(label: str, fn, *args, **kwargs) -> tuple[float, object]:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    print(f"  {label:30s} {elapsed:6.3f}s")
    return elapsed, result


def main() -> int:
    service = AlphaPilotService()

    print("== 数据状态 ==")
    status = service.cache_status()
    print(f"  标的数: {status.get('symbol_count')}, "
          f"K线: {status.get('bar_count')}, "
          f"最新: {status.get('latest_trade_date')}")

    # ----- 信号生成：watchlist -----
    print("\n== 信号生成（watchlist，limit=10）==")
    t_sig, sigs = time_it("signals(limit=10)", service.signals, universe="watchlist", limit=10)
    print(f"  返回 {len(sigs)} 条")

    # ----- 信号生成：all_a -----
    print("\n== 信号生成（all_a，limit=20）==")
    t_all, sigs_all = time_it("signals(limit=20)", service.signals, universe="all_a", limit=20)
    print(f"  返回 {len(sigs_all)} 条")

    # ----- 回测 -----
    print("\n== 回测 ==")
    t_bt, bt = time_it("backtest()", service.backtest)
    summary = bt.get("summary", {})
    print(f"  交易数: {summary.get('trade_count')}, "
          f"胜率: {summary.get('win_rate')}, "
          f"总收益: {summary.get('total_return')}")

    # ----- 汇总 -----
    total = t_sig + t_all + t_bt
    print("\n== 汇总 ==")
    print(f"  signal(limit=10)  : {t_sig:.3f}s")
    print(f"  signal(limit=20)  : {t_all:.3f}s")
    print(f"  backtest          : {t_bt:.3f}s")
    print(f"  --------------------------------")
    print(f"  合计              : {total:.3f}s")

    # ----- 缓存效果对比（同一基准/sector/leader 重复计算）-----
    print("\n== IndicatorCache 效果对比（5 轮×7 只股票 = 35 次 add_indicators）==")
    from alphapilot.config import WATCHLIST
    from alphapilot.strategy.trend20 import add_indicators
    from alphapilot.strategy.cache import IndicatorCache

    market = service.cache.get_bars("000001.SH")
    sector = service.cache.get_bars("159770.SZ")
    leader = service.cache.get_bars("601138.SH")

    # 旧实现：每只股票都重算
    start = time.perf_counter()
    for _ in range(5):
        for _ in WATCHLIST:
            add_indicators(market)
            add_indicators(sector)
            add_indicators(leader)
    elapsed_old = time.perf_counter() - start

    # 新实现：缓存复用
    start = time.perf_counter()
    for _ in range(5):
        cache = IndicatorCache()
        for _ in WATCHLIST:
            cache.get(market)
            cache.get(sector)
            cache.get(leader)
    elapsed_new = time.perf_counter() - start

    speedup = elapsed_old / elapsed_new if elapsed_new > 0 else float("inf")
    print(f"  旧实现（无缓存）: {elapsed_old:.3f}s")
    print(f"  新实现（缓存）  : {elapsed_new:.3f}s")
    print(f"  加速比          : {speedup:.1f}x")

    # 性能目标
    if t_sig > 1.0:
        print(f"\n  ⚠️  signal 超过 1s 目标（实际 {t_sig:.3f}s）")
        return 1
    if t_bt > 10.0:
        print(f"\n  ⚠️  backtest 超过 10s 目标（实际 {t_bt:.3f}s）")
        return 1
    print("\n  ✅ 全部达标（signal<1s, backtest<10s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
