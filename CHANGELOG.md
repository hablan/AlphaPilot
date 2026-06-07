# CHANGELOG — 给 CodeX 看的 2026-06-07 同步

> 本周 Claude 临时代做了 `web/index.html` 大量 UI 改动(本来是 CodeX 范围)。这文档帮你快速了解:
> 1. 6 个 commit 都改了什么
> 2. 新增/修改了哪些"契约"(CSS class、HTML data 属性、行为),不要破坏它们
> 3. 几个特别脆弱的点,改了会让板块弹窗/快捷键弹窗/topbar 布局坏

---

## 1. Commit 速查表

| Commit | 类型 | 改了什么 | 涉及文件 |
|--------|------|---------|---------|
| `b63ac17` | fix | 持仓卡文案(后端 bug 修复)+ 板块强度 tooltip + 门控 pill + 策略表现样本量 + 持仓配色 | `service.py` + `web/index.html` |
| `aec8541` | feat | 持仓仓位占比(后端 + 前端) + 资金流白话文案 + 标记价自动填 + `/api/quote` 端点 | `service.py` + `server.py` + `web/index.html` |
| `ce49326` | refactor | 移除 3 个 ticker 下的"⇄ 点切换基准"提示语 | `web/index.html` |
| `f2957b6` | refactor | 更新按钮缩小 + 删 1-3 天 10 freshness 块 + 补 `.btn-small` / `.btn-secondary` CSS | `web/index.html` |
| `05d588c` | refactor | "更新" 换 🔄 图标 + `.topbar` grid 加列 | `web/index.html` |
| `736dbd8` | fix | 补 `.modal-backdrop` / `.modal` 基础 CSS(弹窗不显示 bug) | `web/index.html` |

---

## 2. 新增/修改的契约(CodeX 必须知道)

### 2.1 CSS class 新增

| Class | 作用 | 谁在用 |
|-------|------|--------|
| `.btn.btn-small` | 28px 高 + 12px 字号 | `theme-toggle` (🌙) / `refresh` (🔄) / `sector-modal-open` |
| `.btn-secondary` | 浅灰底 + 灰边中性按钮 | 同上 |
| `.btn.icon-btn` | 28×28 方形图标按钮 | `refresh` |
| `.gate-pill` / `.gate-ok` / `.gate-bad` | 门控列短词 pill(取代 ✓/✗) | `_gateSummary()` 函数 |
| `.modal-backdrop` / `.modal` | 板块/快捷键弹窗 | `sector-modal-backdrop` / `kbd-help-backdrop` |
| `.holdings-summary` / `.holdings-name` / `.holdings-prices` / `.holdings-meta` | 持仓卡 4 列结构 | `renderHoldingRisks()` |
| `.pnl-soft` / `.up-soft` / `.down-soft` | 浅色浮亏(降低视觉压迫) | 同上 |
| `.sector-hint` | 板块卡标题下小字提示 | `renderSectorRanking()` 末尾动态插入 |
| `.sample-tag` | 策略表现样本量标签(红/黄/绿) | `renderPerformanceCurve()` |

**为什么这次有些 class 没样式也能用?** 之前 `.btn-small` / `.btn-secondary` 被 `<button class="btn-secondary btn-small">` 引用但 CSS 里**没有**对应规则,fallback 到 `.btn` 默认 34px 高度。本次补上了。**之后不要清掉这些规则。**

### 2.2 HTML data 属性新增

| 属性 | 位置 | 触发什么 |
|------|------|---------|
| `data-last-price` | `.pick-row` / `.sector-row` / `.sector-group-row` / `.plan-row` / `.risk-row` | 点击时,通用事件委托会自动填 mark 表单的代码 + 价格 |
| `data-build-id` | `<head>` 里的 `<meta>` | server 注入,前端 fetch `/api/build-id` 对比,不一致就 hard reload |

**CodeX 写新行时如果想用自动填价,只要在 row 元素上加 `data-code` + `data-last-price` 两个属性就够了**,不用写新的 click listener。

### 2.3 后端字段新增

| 字段 | 在哪 | 含义 |
|------|------|------|
| `cost_value` | `_holding_risks()` 返回每条 | 该持仓的成本市值(`cost_price × shares`) |
| `position_share_pct` | 同上 | 占总成本的比例(0-1) |
| `last_price` | `_next_session_plan()` 每条候选 | 用于前端点击 plan-row 填 mark 表单 |

**新增字段都用了默认值,不影响旧调用方。** 但是 `_holding_risks()` 末尾会计算 `total_cost` 来算占比,如果未来 holdings 是空(全部 `cost_price=0`),`total_cost=1` 兜底(避免除零)。

### 2.4 新增 API 端点

- `GET /api/quote?code=xxx` — 返回 `{code, last_price, change_pct, trade_date, has_data}`

---

## 3. 脆弱的 layout(改了会让 UI 坏)

### 3.1 `.topbar` grid 列数必须是 6

```css
.topbar { display: grid; grid-template-columns: minmax(260px, 1fr) repeat(3, 160px) auto auto; }
```

**原因:** DOM 顺序是 `[.title, .ticker, .ticker, .ticker, #theme-toggle, #refresh]`,共 6 个子元素。如果改回 5 列,refresh 会被挤到第 2 行并占满 1fr(变成 876px 宽的横条)。

### 3.2 `.modal-backdrop` / `.modal` 基础 CSS 缺一不可

```css
.modal-backdrop { position: fixed; inset: 0; background: rgba(15,23,42,0.45); z-index: 200; display: flex; align-items: center; justify-content: center; }
.modal-backdrop[hidden] { display: none !important; }
.modal { background: #fff; border-radius: 10px; padding: 20px 24px; max-width: 720px; width: 90%; max-height: 80vh; overflow-y: auto; box-shadow: 0 20px 50px rgba(15,23,42,0.3); }
```

**踩过的坑:** 之前某次整理 CSS 时,只有 `html[data-theme="dark"] .modal` 覆写还活着,基础规则全删了,弹窗 `position: static; z-index: auto` 用户看不到。两个弹窗(板块强度 / 快捷键)用同一套 class,所以改一次两边都坏。

### 3.3 持仓卡 `<span class="holdings-meta">` 现在有 3 行

旧:只有 `severity badge` + `距 MA20` + `alerts`
新:`severity badge` + **仓位占比** + `距 MA20` + `alerts`

如果改这段,不要假设 1 行 — flex column,有视觉间距。

---

## 4. 暗色模式要求

`html[data-theme="dark"]` 覆写已存在的 UI 组件:

- `.ticker` / `.card` / `.modal` / `.risk-row` / `.pick-row` / `.plan-row` / `.sector-row` / `.sector-group-row`
- `.gate-pill.gate-ok` / `.gate-pill.gate-bad`
- `.pnl-soft.up-soft` / `.pnl-soft.down-soft`
- `.data-status`

**CodeX 新增 UI 组件时必须**:
1. 用 CSS 变量(`var(--bg)` / `var(--panel)` / `var(--line)` / `var(--text)` / `var(--muted)` / `var(--green)` / `var(--red)`)而不是写死颜色
2. 显式加 `html[data-theme="dark"] .your-class { ... }` 覆写(比如反色文字、浅边框)

---

## 5. 行为契约(其他 agent 改动时注意)

### 5.1 标记买入自动填价

通用事件委托在 1444 行附近:
```js
const input = document.getElementById('mark-code');
const priceInput = document.getElementById('mark-price');
const lastPrice = target.dataset.lastPrice;
if (input) input.value = code;
if (priceInput && lastPrice) {
  priceInput.value = lastPrice;
  priceInput.placeholder = `最新收盘价 ${lastPrice}`;
  showToast(`已填入 ${code} · 价格默认最新收盘价,可手动调整`, 'success');
}
```

**任何新增行元素如果想让点击触发此行为,必须:**
- 有 `.pick-row` / `.sector-row` / `.sector-group-row` / `.plan-row` / `.risk-row` class(在 1431 行的 selector 里)
- 或:扩展那个 selector 加上你的新 class
- 元素上有 `data-code` + `data-last-price`

### 5.2 加载中按钮旋转

```css
.btn.icon-btn.loading { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
```

JS 在 `refreshAndReload()` 加 `btn.classList.add('loading')` / 完成后 `remove('loading')`。**如果 CodeX 写新的"加载中"按钮,沿用这个 class。**

### 5.3 暗色模式切换

按钮 `id="theme-toggle"` 已在事件委托里;切换 `<html data-theme="dark">` / `<html data-theme="light">` 即可整页换色。**新增 UI 元素用 CSS 变量,不要写死颜色。**

---

## 6. 测试

- 249 个测试,248 个通过(1 个预存的 `test_paper_equity_curve_after_paper_buy` 失败与本次无关)
- CodeX 写新 UI 时不需要写 Python 测试,但**手动验证** checklist:
  - [ ] 暗色模式下文字可读
  - [ ] 持仓卡 4 列对齐没乱
  - [ ] 弹窗在 viewport 任意位置能关闭(ESC / 点 backdrop)
  - [ ] 暗色模式切换不丢焦点

---

## 7. 下一步建议(给 CodeX)

如果你要继续优化首页,优先级高的几个:

1. **暗色模式覆盖审计** — 还有几个组件没覆写(比如 `data-status` 的 sample warning 边框)
2. **板块强度空状态文案** — 当 19 个 ETF 全缺失时,目前是"暂无板块数据",可以更友好
3. **快捷键面板的样式** — 跟数据卡风格不统一,可以参考 modal 的白底 + 阴影风格
4. **配置页配色** — 暗色模式下有几个 input 看不清

---

> 有疑问先看 `git log -p -- alphapilot/web/index.html` 最近的 6 个 commit,再回看本文档第 3 节(脆弱 layout)。
