#!/usr/bin/env bash
# 安装 AlphaPilot 每日 15:30 增量更新任务到 macOS launchd
# 用法：bash scripts/install_launchd.sh [provider] [universe]
#   provider 默认 auto（推荐）
#   universe 默认 watchlist

set -euo pipefail

PROVIDER="${1:-auto}"
UNIVERSE="${2:-watchlist}"
LABEL="com.alphapilot.refresh"

# 解析项目根目录和 Python 路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$(command -v python3)"
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: python3 not found in PATH" >&2
  exit 1
fi

# 日志目录
LOG_DIR="$HOME/Library/Logs/AlphaPilot"
mkdir -p "$LOG_DIR"

# 渲染 plist
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

# 卸载已有的（如果存在）
launchctl unload "$PLIST" 2>/dev/null || true

# 加载
launchctl load "$PLIST"

cat <<EOF
✓ AlphaPilot 调度任务已安装
  Label:    $LABEL
  Provider: $PROVIDER
  Universe: $UNIVERSE
  工作目录:  $WORKDIR
  Python:   $PYTHON
  日志:     $LOG_DIR/refresh.log
  plist:    $PLIST

调度：每个交易日（周一-周五）15:30 自动运行增量更新
查看状态：launchctl list | grep alphapilot
手动触发：launchctl start $LABEL
查看日志：tail -f $LOG_DIR/refresh.log
卸载：    bash scripts/uninstall_launchd.sh
EOF
