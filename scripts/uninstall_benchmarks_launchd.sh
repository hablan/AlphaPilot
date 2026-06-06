#!/usr/bin/env bash
# 卸载 AlphaPilot 基准调度任务

set -euo pipefail

LABEL="com.alphapilot.refresh.benchmarks"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$PLIST" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✓ $LABEL 已卸载"
else
  echo "未找到 $PLIST，无需卸载"
fi
