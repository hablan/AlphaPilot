# AlphaPilot 数据源与本地 K 线缓存方案

## 目标

MVP 阶段抓取并缓存近 3 年 A 股日线 K 线，供 Trend20 信号、回测、持仓盈亏和复盘使用。策略计算只读取本地缓存，不在计算过程中直接请求外部行情接口，保证结果可复现。

## 数据源策略

| 优先级 | 数据源 | 定位 |
|---|---|---|
| P0 | `auto` 组合源 | MVP 真实数据默认入口，沪深标的优先 Tencent curl，北交所标的优先 Sina，再兜底 EastMoney IPv4 curl、AkShare；不使用模拟数据兜底。 |
| P1 | Tencent curl | 当前本地环境中沪深股票、ETF、指数日线响应较稳定，使用 curl、重试、退避、分段抓取。 |
| P2 | Sina curl | 北交所日线兜底源，使用更轻量的 curl 请求，避免部分请求头触发拒绝访问。 |
| P3 | EastMoney curl | 备选源，强制 IPv4、重试、退避。 |
| P4 | AkShare | 备选源。Python 网络链路可用时作为单标的失败兜底。 |
| P5 | 授权数据源 | 商业化前接入有授权、配额和服务承诺的数据源，替换免费接口作为主源。 |

商业化前需要重新评估数据授权。MVP 先用于本地研究、回测和产品验证；交易相关页面不能在真实数据抓取失败时静默使用 `sample` 模拟数据。

## 缓存范围

- 时间范围：默认近 3 年。
- 频率：日线 `1d`。
- 复权：策略默认使用前复权 `qfq`。
- 标的范围：
  - `sample`：开发测试样本。
  - `watchlist`：用户自选、策略池、基准指数、板块 ETF。
  - `all_a`：全 A + 常用 ETF + 基准指数。

开发阶段默认先跑 `watchlist`，正式初始化再跑 `all_a`。

## 存储设计

```text
data/market/
  parquet/kline_1d/provider=akshare/adjust=qfq/symbol=000001.parquet
  parquet/kline_1d/provider=akshare/adjust=qfq/symbol=600000.parquet
  market.duckdb
  manifests/fetch_runs.jsonl
```

唯一键：

```text
symbol + trade_date + frequency + adjust_type + provider
```

当前 MVP 为了交付速度，行情、状态、买卖标记先统一写入 `data/alphapilot.sqlite`。扩大到更多年份或分钟级数据时，再迁移到 `DuckDB + Parquet`；策略层仍通过缓存接口读取，避免影响产品功能。

## 初始化流程

1. 获取交易日历和标的池。
2. 计算 `start_date = today - 3 years`，`end_date = today`。
3. 按标的抓取日线 K 线。
4. 每个真实数据请求失败时先重试再切换备选源；手动开启 `--sleep` 时只在真实请求后等待，跳过已缓存标的不等待。
5. 标准化字段：代码、日期、OHLCV、成交额、复权类型、真实数据源、抓取批次。
6. 校验数据：空结果、重复日期、空值、OHLC 合法性、异常涨跌幅、最新交易日缺失。
7. 写入本地缓存。
8. 记录抓取任务结果、每个标的最后一次抓取状态、失败原因和实际数据源。

全市场补齐推荐命令：

```bash
python3 -m alphapilot init-data --provider auto --years 3 --universe all_a --resume
python3 -m alphapilot init-data --provider auto --years 3 --universe all_a --incomplete-only --sleep 0.2
```

`--resume` 和 `--incomplete-only` 都会跳过已有近端完整缓存的标的；区别是后者用于“只修复缺口”的运维语义，适合失败后反复补齐。

## 增量更新

每个交易日收盘后更新：

1. 读取每个标的最新缓存日期。
2. 从最新日期往前回补 5 个交易日。
3. 抓取到当前交易日。
4. 按唯一键 upsert，覆盖可能被数据源修正的历史行。
5. 若数据源失败，保留旧缓存并标记“数据未更新”，不静默生成新信号。

## 开发任务拆分

1. 定义 `MarketDataProvider` 接口。
2. 实现 `AkShareProvider`。
3. 实现 K 线标准化器和字段映射。
4. 实现 Parquet 写入与 DuckDB 查询视图。
5. 实现初始化命令：`alphapilot data init --years 3 --universe watchlist --adjust qfq`。
6. 实现增量命令：`alphapilot data update --lookback-trading-days 5`。
7. 实现数据质量报告：成功数、失败数、缺失日期、异常标的。
8. 将 Trend20 指标计算改为只读本地缓存。

## 验收标准

- 可以在本地初始化近 3 年日线缓存。
- 重复初始化不会产生重复 K 线。
- 增量更新只回补最近交易日，并能覆盖修正数据。
- 单个数据源失败时能记录失败标的，不影响已有缓存读取。
- 组合源成功时，K 线行记录实际成功的数据源，而不是笼统写成 `auto`。
- Trend20 信号和回测记录包含 `provider`、`adjust_type`、`data_version`、`date_range`。
- 缓存缺失或过期时，页面明确显示数据状态，不生成新的研究输出。

## 当前长期方案

短期先把下载链路做稳：`auto` 组合源、Tencent 分段 curl、Sina 北交所兜底、EastMoney IPv4 curl、重试退避、按需限速、单标的状态表、本地缓存兜底和缺口补跑。这样可以解决本地代理、IPv6、单接口限流、北交所兼容性和短时间连续请求失败带来的不稳定。

中期扩大到全 A 或更多年份时，需要增加任务队列、断点续跑、失败重试队列、交易日历校验、增量回补和缓存压缩。数据源仍失败时，只保留旧缓存并提示“数据未更新”，不生成新的交易研究结论。

长期进入商业化或真实交易辅助前，应接入授权数据源作为主源，免费接口只作为辅助校验源；同时做双源抽样比对、复权一致性校验、停复牌/除权处理和数据版本追溯。

## 资金流向

资金流向不再依赖单一免费网页接口作为主链路。免费东财/同花顺接口在本地环境会出现断连、空响应或反爬拦截，只适合作为诊断和临时补充。

MVP 当前执行方案：

- 策略池近一个月资金流：使用 `eastmoney_fund_flow_history`，只覆盖当前自选/策略池标的；适合产品验证和页面展示。
- 全 A 免费批量不作为主链路：本地 50 标的压力测试出现远端空响应，且耗时较长，只能用于诊断或小批量补充。
- 页面数据状态需要同时展示 K 线缓存和资金流缓存，让用户知道哪些数据已经入库。

全 A 稳定方案：

- 近一个月全 A 资金流：使用 `tushare_moneyflow_dc`，按交易日批量请求，约 20 个请求即可覆盖全 A。
- 环境变量：`TUSHARE_TOKEN` 或 `TS_TOKEN`。
- 单位规范：Tushare `moneyflow_dc` 金额字段原始单位为万元，入库前统一换算为元。
- 推荐命令：

```bash
python3 -m alphapilot init-fund-flow --provider tushare_moneyflow_dc --universe all_a --days 35 --resume
```

当前策略池可先执行：

```bash
python3 -m alphapilot init-fund-flow --provider eastmoney_fund_flow_history --universe watchlist --days 35 --resume
```

如果没有授权 token，系统必须明确失败，不生成伪资金流数据，也不把成交量或涨跌幅推算结果写成真实资金流。
