#!/usr/bin/env bash
# 安装 AlphaPilot 每日 15:35 增量更新基准 ETF 任务到 macOS launchd
# 紧跟 watchlist refresh 任务（15:30）之后 5 分钟
# 用法：bash scripts/install_benchmarks_launchd.sh [provider] [universe]
#   provider 默认 auto
#   universe 默认 benchmarks（拉所有 29 个候选基准）

set -euo pipefail

PROVIDER="${1:-auto}"
UNIVERSE="${2:-benchmarks}"
LABEL="com.alphapilot.refresh.benchmarks"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$(command -v python3)"
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: python3 not found in PATH" >&2
  exit 1
fi

LOG_DIR="$HOME/Library/Logs/AlphaPilot"
mkdir -p "$LOG_DIR"

TEMPLATE="$WORKDIR/launchd/$LABEL.plist.template"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: template not found at $TEMPLATE" >&2
  exit 1
fi

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__PROVIDER__|$PROVIDER|g" \
    -e "s|__UNIVERSE__|$UNIVERSE|g" \
    -e "s|__WORKDIR__|$WORKDIR|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$TEMPLATE" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

cat <<EOF
✓ AlphaPilot 基准调度任务已安装
  Label:    $LABEL
  Provider: $PROVIDER
  Universe: $UNIVERSE
  工作目录:  $WORKDIR
  Python:   $PYTHON
  日志:     $LOG_DIR/refresh-benchmarks.log
  plist:    $PLIST

调度：每个交易日（周一-周五）15:35 自动运行基准增量更新
查看状态：launchctl list | grep alphapilot
手动触发：launchctl start $LABEL
查看日志：tail -f $LOG_DIR/refresh-benchmarks.log
卸载：    bash scripts/uninstall_benchmarks_launchd.sh
EOF
