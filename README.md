# AlphaPilot Trend20 MVP

AlphaPilot MVP converts the Trend20 rules into a local, testable workflow:

- local K-line cache
- Trend20 signal generation
- manual buy/sell marks
- basic backtest metrics
- lightweight local web UI

## Quick Start

```bash
python3 -m alphapilot init-data --provider sample --years 3 --universe watchlist
python3 -m alphapilot signals
python3 -m alphapilot backtest
python3 -m alphapilot serve --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Data Source

The offline MVP uses `sample` deterministic K-line data so tests and demos do not depend on network availability. For real cached data, use the resilient provider chain. It tries Tencent curl first for most A-shares, Sina first for BJ shares, then EastMoney curl and AkShare when available. It records per-symbol fetch status and keeps using the last local cache when a provider fails:

```bash
python3 -m alphapilot init-data --provider auto --years 3 --universe watchlist
```

For all-A initialization or repair, use resumable mode. Cached symbols are skipped without waiting, so the command can be rerun safely:

```bash
python3 -m alphapilot init-data --provider auto --years 3 --universe all_a --resume
python3 -m alphapilot init-data --provider auto --years 3 --universe all_a --incomplete-only --sleep 0.2
```

Single providers can still be used for diagnosis:

```bash
python3 -m alphapilot init-data --provider tencent --years 3 --universe watchlist --sleep 1
python3 -m alphapilot init-data --provider eastmoney --years 3 --universe watchlist --sleep 2
python3 -m alphapilot init-data --provider akshare --years 3 --universe watchlist
```

Fund-flow data is optional in the MVP because free endpoints are less stable and usually expose a shorter history. For the current strategy pool, cache the latest month with the EastMoney history endpoint:

```bash
python3 -m alphapilot init-fund-flow --provider eastmoney_fund_flow_history --universe watchlist --days 35 --resume
```

For stable one-month all-A fund flow, use an authorized Tushare token:

```bash
export TUSHARE_TOKEN=your_token
python3 -m alphapilot init-fund-flow --provider tushare_moneyflow_dc --universe all_a --days 35 --resume
```

Tushare `moneyflow_dc` returns amount fields in 10k CNY units; AlphaPilot stores them as CNY in `fund_flow_daily`.

Free diagnostic providers are still available, but they should not be treated as reliable production inputs or a dependable full-market batch source:

```bash
python3 -m alphapilot init-fund-flow --provider eastmoney_fund_flow_history --universe watchlist --days 35
python3 -m alphapilot init-fund-flow --provider eastmoney_fund_flow_history --universe all_a --days 35 --max-symbols 50
```

Commercial or wider use should switch to an authorized market data provider with stable quota, authorization, and service commitments. The MVP does not silently fall back to synthetic sample data for trade-facing signals.

## Scheduled Refresh (macOS launchd)

After `init-data` populates the cache, install a launchd agent that re-runs the incremental update at 15:30 every weekday (right after market close):

```bash
# 安装：每个交易日 15:30 自动增量更新
bash scripts/install_launchd.sh                       # provider=auto, universe=watchlist
bash scripts/install_launchd.sh auto all_a            # 自定义参数

# 查看状态
launchctl list | grep alphapilot

# 手动触发
launchctl start com.alphapilot.refresh

# 查看日志
tail -f ~/Library/Logs/AlphaPilot/refresh.log

# 卸载
bash scripts/uninstall_launchd.sh
```

The plist template at `launchd/com.alphapilot.refresh.plist.template` calls `python3 -m alphapilot refresh` which:

- skips already-up-to-date symbols
- covers intraday bars with real-time snapshots when akshare is available
- syncs fund-flow data if `--include-fund-flow` is set in the rendered plist
- prints a JSON summary with `success_count` / `skipped_count` / `refreshed_count` / `failure_count`

launchd calendar events fire Mon–Fri at 15:30, so weekend days are automatically skipped. The refresh command itself checks `is_market_open()` for graceful handling of public holidays; on holidays it still runs but `incremental_update` is a no-op for already-current symbols.

## Backtest Caveats

`python3 -m alphapilot backtest` runs a single-symbol walk-forward on cached K-lines. It is a **research / sanity-check tool**, not a production-grade backtester. Known limitations:

| Limitation | Why it matters | Workaround |
|---|---|---|
| No suspension handling | Backtest treats suspended days as tradable. In reality, exit signals on suspension days are missed. | Skip suspended symbols or apply manual filters. |
| No dividend / split adjustment | Entry price is not adjusted for ex-dividend dates. Reported PnL may overstate by dividend amount. | Use `qfq` (forward-adjusted) for trend signals, but note backtest PnL doesn't account for distributions. |
| Single-position assumption | Each symbol is treated independently. Cross-symbol capital allocation is not simulated. | Manually sum across symbols. |
| Constant 1000 shares | Fixed position sizing ignores cash management and liquidity. | Refactor `Trend20Backtester.shares_per_trade` for capital-aware sizing. |
| Slippage assumption is static | Uses `config.slippage_rate` for every trade. Real slippage varies by volume. | Tune per-symbol in `BacktestConfig`. |
| No walk-forward / out-of-sample | Tests in-sample. Past winners may overfit. | Use multiple time windows; consider a separate validation script. |
| Strategy settings version mismatch | Backtest uses current `Trend20Settings`, not the historical version at signal time. | If you change settings, prior backtest results become incomparable. |

**Rule of thumb**: trust backtest results only when the backtest window covers at least 6 months of data and the strategy settings have been stable across that window.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Runtime Checks

With the local server running:

```bash
curl http://127.0.0.1:8765/api/status
curl http://127.0.0.1:8765/api/dashboard
```

`/api/status` reports cache health, latest trade date, data source mix, and the most recent fetch result. If a live provider only partially succeeds, the UI shows a data status banner and keeps using the latest local cache for failed symbols.
