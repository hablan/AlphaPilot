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

## 测试原则

- 新功能必须有单元测试
- bug 修复先写一个失败的测试，再修
- 关键模块（providers、config、journal）覆盖率 ≥ 70%

## 依赖管理

- `pyproject.toml` 中 `dependencies` 是必选
- 任何新依赖必须先讨论
- 优先用标准库
