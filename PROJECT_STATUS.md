# AlphaPilot 项目状态

> 双 AI 协作（Claude / CodeX）的唯一事实源。每次开始工作前请先读本文档。

---

## 当前阶段

**阶段 1：稳定性与硬编码修复**（进行中）

完整优化计划见 `/Users/henry/.claude/plans/alphapilot-playful-boot.md`。

---

## 项目速览

- **类型**：Python 量化交易策略系统（A股 Trend20 策略）
- **Python**：>= 3.9
- **核心依赖**：pandas, numpy（数据）；akshare, pyarrow（可选）
- **入口**：`python3 -m alphapilot {init-data,status,signals,backtest,mark,serve}`
- **Web 端口**：默认 8765

---

## 分工建议（非强制）

| Agent | 建议负责 |
|-------|----------|
| **Claude** | Python 后端、策略/回测/数据层、API、测试 |
| **CodeX** | Web UI（`web/index.html`、`prototypes/alphapilot-v2/`）、前端样式、可视化 |
| 双方可接 | CLI 改进、文档、bug 修复（先认领先得） |

---

## 待办清单

### 阶段 1：稳定性修复
- [x] #bug [claude] 边界检查：`service.py` benchmark_cards 添加空数据保护
- [x] #bug [claude] 异常分类：`server.py` 区分 ValueError / RuntimeError / 兜底
- [x] #refactor [claude] 提取 `_compact_error` 到 `data/utils.py`
- [x] #refactor [claude] 常量化：端口、信号限制统一到 `config.py`
- [x] #refactor [claude] 策略参数验证迁移到 `Trend20Settings.__post_init__`

### 阶段 2：测试覆盖
- [x] #test [claude] providers 单元测试（52 个，覆盖 62%）
- [x] #test [claude] config 单元测试（14 个，覆盖 100%）
- [x] #test [claude] journal store 测试（18 个，覆盖 100%）
- [x] #test [claude] trend20 边界测试（13 个新增，总 16 个，覆盖 96%）
- [x] #test [claude] server 测试去全局（15 个新增，总 16 个）

### 阶段 3：性能优化
- [x] #perf [claude] 新建 `IndicatorCache` 类，缓存 `add_indicators` 结果
- [x] #perf [claude] service 复用 IndicatorCache，消除 4 次重复计算
- [x] #perf [claude] backtest 预计算 market/sector/leader 指标 + searchsorted 切片
- [x] #perf [claude] 添加 `(symbol, fetched_at)` 复合索引
- [x] #perf [claude] service 信号生成传入 `end_date=as_of`
- [x] #perf [claude] 性能对比脚本 `tests/perf_signal.py`（IndicatorCache 加速 19x）

### K 线增量更新 + 当日 K 线
- [x] #kline [claude] `AkShareProvider.fetch_intraday_snapshot()` 实时快照覆盖当日 K 线
- [x] #kline [claude] `is_market_open()` / `next_market_open()` 工具函数
- [x] #kline [claude] `initialize_market_cache(incremental=True)` 起点 = `last_trade_date+1`
- [x] #kline [claude] `_apply_intraday_override` 盘中覆盖当日
- [x] #kline [claude] 返回 `mode` / `market_status` / `refreshed_count` 字段

### launchd 调度（macOS）
- [x] #schedule [claude] `cli.py refresh` 子命令（增量+资金流）
- [x] #schedule [claude] `launchd/com.alphapilot.refresh.plist.template` 模板
- [x] #schedule [claude] `scripts/install_launchd.sh` + `uninstall_launchd.sh`
- [x] #schedule [claude] 测试：CLI / plist 渲染 / launchd Weekday 字段

### 自用版（生产可用）
- [x] #safety [claude] 拒绝 sample 数据源（生产路径强制走真实源，ALLOW_SAMPLE_DATA=1 解封）
- [x] #data [claude] A 股交易日历（2025-2026 节假日 + 调休 + weekend）
- [x] #ux [claude] 首页持仓总览：实时市值/已实现盈亏/下一交易日候选
- [x] #ops [claude] 服务 KeepAlive：launchd KeepAlive=true + /healthz 端点
- [x] #docs [claude] README 文档化回测 7 项局限
- [x] #data [claude] 数据版本可视化：freshness 摘要（lag_distribution + is_stale）
- [x] #fix [claude] 2026-06-03 恢复 service 公共方法：mark_trade / backtest / marks / _portfolio_summary / _next_session_plan / _loss_streak 改回 AlphaPilotService 实例方法（之前误改为模块级函数，导致 server.py / cli.py 的 self.service.xxx() 调用链断），同步更新 test_portfolio_summary 调用方式。修后 227 测试全绿。
- [x] #data [claude] 2026-06-03 修复 _has_recent_bars 1 行误判 bug：加 MIN_BARS_FOR_RECENT=60 门槛，防止 sample 残留导致 launchd 永久跳过标的；同时把 trend20 settings 误改全部 false 还原为默认。
- [x] #verify [claude] 2026-06-03 端到端真实数据验证：watchlist 10 个标的 730+ 行真实 K 线；Trend20 在真实数据上行为正确（市场无金叉机会 → 全 SKIP，浮盈持仓 → EXIT_ALERT）；backtest 输出 7 笔交易（100% win, take_profit 触发）；dashboard 渲染正常。231 测试全绿。
- [x] #dx [claude] 2026-06-03 修复命令：加 alphapilot/__main__.py + pyproject [project.scripts] 让 `python3 -m alphapilot` 和 `alphapilot` 都能跑；更新 README/launchd 模板所有引用。
- [x] #safety [claude] 2026-06-03 settings 误改保护：service.reset_strategy_config() + POST /api/config/reset 端点 + cache.delete_setting()。手抖/测试把 settings 改坏后一键恢复默认（关键场景：本次 P0 调试时 settings 被全改 false 就是缺这个保护）。新增 3 个测试。234 测试全绿。

### 阶段 4：架构重构
- [x] #arch [claude] 创建 `StrategyEngine` ABC + 注册表（trend20 注册为内置）
- [x] #arch [claude] 创建 `DataProvider` 抽象 + 注册表（行情/资金流各 7 个）
- [x] #arch [claude] 创建 `alphapilot/i18n.py`，所有用户可见中文消息集中
- [x] #test [claude] mock strategy 测试（13 个新测试，验证 ABC/注册表/可插拔）
- [ ] #arch [skipped] Service 拆分（风险高，保留 facade）
- [ ] #arch [skipped] Handler 完全依赖注入（已有 `set_service()`，类级 service 保留兼容）

### 阶段 4 暂不做（已评估风险）
- Service 拆分为 DataService/SignalService/BacktestService/PortfolioService：service.py 是所有调用核心，拆分涉及 API 兼容和线程模型，保留当前 facade
- Handler 改完全 DI：目前 `set_service()` 方法已经支持实例级注入，类级 `service` 作为兜底

---

## 已完成

- **2026-06-02 [claude]** 阶段 1：5 个稳定性修复全部完成，24 个单元测试全绿
- **2026-06-02 [claude]** 阶段 2：5 个测试模块新增/重写完成，共 136 个测试全绿，整体覆盖率 70%
- **2026-06-02 [claude]** 阶段 3：6 个性能优化全部完成，signal<100ms, backtest<500ms
- **2026-06-02 [claude]** 阶段 4：4 项架构改进完成（ABC/i18n/mock 测试），149 个测试全绿
- **2026-06-02 [claude]** K 线增量更新 + 当日 K 线：upsert + snapshot 覆盖机制，新增 19 个测试
- **2026-06-02 [claude]** launchd 调度：每日 15:30 收盘后自动增量更新，新增 9 个测试，177 个测试全绿
- **2026-06-03 [claude]** 修复 service.py 误改：6 个方法（mark_trade/backtest/marks/_portfolio_summary/_next_session_plan/_loss_streak）从模块级函数还原为 AlphaPilotService 实例方法，修复 11 个失败测试，227 测试全绿

## 性能基准

| 操作 | 时间 | 目标 |
|------|------|------|
| signal(limit=10) | 35ms | <1s ✅ |
| signal(limit=20, all_a) | 64ms | <1s ✅ |
| backtest | 446ms | <10s ✅ |
| IndicatorCache 复用 35 次 | 4ms（19x 加速） | — |

## 测试统计

| 阶段 | 测试数 | 累计 | 关键模块覆盖率 |
|------|--------|------|--------------|
| 初始 | 24 | 24 | — |
| 阶段 1 | 0（+5 修复） | 24 | — |
| 阶段 2 | +112 | 136 | 70%（config/journal 100%） |
| 阶段 3 | 0 | 136 | 70% |
| 阶段 4 | +13 | 149 | 70% |

---

## 决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-06-02 | 选择"综合推进 + 小步迭代"模式 | 用户希望同时推进质量、测试、性能、架构，但保持 API 兼容 |
| 2026-06-02 | 不引入新三方库 | MVP 阶段保持依赖最小化 |
| 2026-06-02 | 采用文件标记法分配任务（`[claude]`/`[codex]`） | 暂无外部看板工具 |
| 2026-06-03 | 优先恢复测试全绿（不引入新功能） | 发现 11 个测试失败（service 方法被误改模块级），影响 HTTP /api/mark 等生产路径；先修测试再谈下一阶段 |

---

## 注意事项

- 阶段 2 完成后才能动阶段 3（先有测试再做性能优化）
- 改动前确认本文件中没有其他 agent 正在做同样的任务
- 完成一项后，在 commit message 标注 `[claude]` 或 `[codex]`
