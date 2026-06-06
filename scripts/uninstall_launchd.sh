#!/usr/bin/env bash
# 卸载 AlphaPilot launchd 任务
set -euo pipefail

for LABEL in "com.alphapilot.refresh" "com.alphapilot.serve"; do
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    if [[ ! -f "$PLIST" ]]; then
        echo "  · $LABEL 未安装"
        continue
    fi
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "  ✓ $LABEL 已卸载"
done
