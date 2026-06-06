#!/usr/bin/env bash
# 安装 AlphaPilot Web 服务到 macOS launchd（带 KeepAlive 自动拉起）
# 用法：bash scripts/install_serve_launchd.sh [host] [port]
#   host 默认 127.0.0.1
#   port 默认 8765

set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-8765}"
LABEL="com.alphapilot.serve"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$(command -v python3)"

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
    -e "s|__HOST__|$HOST|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|__WORKDIR__|$WORKDIR|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$TEMPLATE" > "$PLIST"

# 卸载已有的
launchctl unload "$PLIST" 2>/dev/null || true

# 加载
launchctl load "$PLIST"

cat <<EOF
✓ AlphaPilot Web 服务已安装（带 KeepAlive）
  Label:    $LABEL
  地址:     http://$HOST:$PORT
  工作目录:  $WORKDIR
  Python:   $PYTHON
  日志:     $LOG_DIR/serve.log
  plist:    $PLIST

KeepAlive=true：服务挂了会自动拉起（10s 间隔）
开机自启：RunAtLoad=true
健康检查：curl http://$HOST:$PORT/healthz
查看状态：launchctl list | grep alphapilot
查看日志：tail -f $LOG_DIR/serve.log
手动停止：launchctl unload $PLIST
卸载：     bash scripts/uninstall_serve_launchd.sh
EOF
