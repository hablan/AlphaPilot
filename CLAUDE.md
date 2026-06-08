# CLAUDE.md — AlphaPilot 协作约定

> 适用于所有 AI agent（Claude / CodeX / 其他）共同迭代本项目。

---

## 项目类型

Python 量化交易策略系统。A 股 Trend20 策略。MVP 阶段。

## 核心命令

```bash
# 在 /Users/henry/Applications/AlphaPilot 目录下
python3 -m unittest discover -s tests          # 跑全部测试
python3 -m alphapilot status                    # 数据状态
python3 -m alphapilot signals --limit 5         # 生成信号
python3 -m alphapilot backtest                  # 跑回测
python3 -m alphapilot serve                     # 启动 Web UI
```

## 代码风格

- **格式化**：PEP 8
- **类型注解**：所有公共方法必须有返回类型
- **命名**：snake_case（函数/变量）、PascalCase（类）、UPPER_SNAKE（常量）
- **导入顺序**：标准库 → 第三方 → 本地
- **docstring**：中文，单行即可
- **错误处理**：使用具体异常类型，不用裸 `except`

## 分支策略

- `main`：稳定分支
- `claude/xxx` 或 `codex/xxx`：agent 工作分支
- 完成阶段后通过 PR 合并

## Claude / CodeX 协作边界

- **Claude 主责**：Python 后端、策略逻辑、数据源、API、测试、launchd、文档同步。
- **CodeX 主责**：Web UI（`alphapilot/web/index.html`、`prototypes/alphapilot-v2/`）、前端交互、布局、暗色模式、可视化和 UI 回归验证。
- `alphapilot/web/index.html` 是高冲突文件。Claude 默认不要改；如后端功能必须临时补 UI，只做最小改动，并同步记录新增/修改的 class、id、data 属性和行为契约。
- 跨前后端功能先由 Claude 完成 API 契约和测试，再由 CodeX 接 UI。
- 开始任务前先在 `PROJECT_STATUS.md` 认领；完成后写 commit id。
- Claude 使用 `claude/xxx` 分支，CodeX 使用 `codex/xxx` 分支；不要在 `main` 上并行修改同一批文件。

## Commit 格式

```
[claude|codex] <type>: <description>

<可选 body>
```

类型：`feat` / `fix` / `refactor` / `test` / `docs` / `perf`

示例：
```
[claude] fix: service.py 边界检查防止空数据崩溃
```

## Git 工作流与回滚约定

仓库 `https://github.com/hablan/AlphaPilot`（main 分支）。所有改动必须先 commit 再 push。

**日常流程**（AI agent 必读）：
```bash
# 1. 改完代码后跑测试
python3 -m unittest discover -s tests
# 2. 确认无失败后，stage + commit（commit message 遵循上面的格式）
git add -A
git commit -m "[claude] type: 简短描述"
# 3. 推送
git push
```

**快速回滚锚点**：当前基线 `efa2dde`（init commit）。
```bash
# 改坏了想回到上次稳定状态
git reset --hard efa2dde          # 全部还原
# 或只还原某个文件
git checkout efa2dde -- alphapilot/web/index.html
# 或回滚最近一次 commit
git revert HEAD
```

**遇到错误时**（比如页面卡 loading）：
```bash
# 直接看 build-id 是否变了（server 重启过）
git log --oneline -5
# 看本地未推送的修改
git diff
# 还原所有未提交的修改
git checkout .
```

**安全回滚原则**：
- 默认先执行 `git status --short` 和 `git diff`，确认没有其他 agent 或用户的未提交改动。
- `git reset --hard`、`git checkout .`、`git push --force` 属于破坏性命令，除非用户明确要求，不要执行。
- 如只需撤销自己刚改的单个文件，优先用补丁反向修改，避免覆盖他人改动。

**禁止**：
- 不要把 `.env`、`data/*.db`、API token commit 进去（已被 `.gitignore` 保护，但仍要警觉）
- 不要 `git push --force`，会覆盖远程历史导致别人代码丢失
- 不要用 `git commit --amend` 改已经 push 的 commit

## 任务分配

在 `PROJECT_STATUS.md` 中用文件标记法：

- 未认领：`- [ ] #tag: 任务`
- 已认领：`- [ ] #tag [claude]: 任务`
- 已完成：`- [x] #tag [claude]: 任务 (commit: xxx)`

## 兼容性约束

- CLI 命令、参数不变
- HTTP 路由不变
- 数据库 schema 兼容（添加字段时用默认值）
- 配置文件键名不变

## 前端样式约定 (2026-06-07 更新)

`web/index.html` 是单文件 SPA(嵌入式 CSS+JS),无构建步骤。新增 UI 时遵循:

**可复用 class (本次会话固化):**
- `.btn.btn-small` — 28px 高, 12px 字号(普通按钮)
- `.btn-secondary` — 浅灰底 + 灰边(中性按钮,不要用 `.btn.primary` 当中性按钮)
- `.btn.icon-btn` — 28×28 方形图标按钮;loading 时配合 `@keyframes spin` 旋转
- `.gate-pill` / `.gate-ok` / `.gate-bad` — 信号门控列的短词 pill(取代 ✓/✗),hover `title` 显示完整定义
- `.modal-backdrop` / `.modal` — 板块/快捷键弹窗;**这两个 class 的基础规则脆弱,改了会同时让两个弹窗坏**
- `.holdings-summary` / `.holdings-name` / `.holdings-prices` / `.holdings-meta` — 持仓卡 4 列结构(已含仓位占比行)

**隐藏行为契约:**
- 行元素(`.pick-row` / `.sector-row` / `.plan-row` / `.risk-row`)若有 `data-code` + `data-last-price`,点击会自动填入 mark 表单代码+价格(无需自己写 listener)
- `<meta name="build-id">` + `/api/build-id` 是自动 reload 机制,改了会让前端缓存问题重现
- `html[data-theme="dark"]` 暗色覆写:新增 UI 组件时**必须**同步加暗色样式,否则切到暗色模式会发白

**脆弱的 layout:**
- `.topbar` grid 模板列数 = 6(`minmax(260px, 1fr) repeat(3, 160px) auto auto`),不要改回 5(改回会让"更新"按钮被挤到第 2 行占满 1fr)
- `<span class="holdings-meta">` 现在含 3 行(severity badge / 仓位占比 / MA20 距),不要假设只有 1 行
- `<span class="sector-hint">` 是板块强度卡的小字提示,改这块布局需要保留

## 测试原则

- 新功能必须有单元测试
- bug 修复先写一个失败的测试，再修
- 关键模块（providers、config、journal）覆盖率 ≥ 70%

## 依赖管理

- `pyproject.toml` 中 `dependencies` 是必选
- 任何新依赖必须先讨论
- 优先用标准库
