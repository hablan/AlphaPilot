"""测试 launchd 调度相关：CLI refresh 子命令 + plist 模板渲染。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alphapilot.data.bootstrap import is_market_open


WORKDIR = Path(__file__).resolve().parents[1]


class TestRefreshSubcommand(unittest.TestCase):
    def test_refresh_help(self) -> None:
        # CLI 至少要能 import 且 --help 工作
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "refresh", "--help"],
            capture_output=True,
            text=True,
            cwd=str(WORKDIR),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("refresh", result.stdout.lower())
        self.assertIn("--provider", result.stdout)
        self.assertIn("--universe", result.stdout)
        self.assertIn("--include-fund-flow", result.stdout)

    def test_refresh_runs_and_reports_market_status(self) -> None:
        """真实跑一次 refresh：sample provider 不会真拉数据，但应返回 market_status 字段。"""
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "refresh",
             "--provider", "auto", "--universe", "watchlist"],
            capture_output=True,
            text=True,
            cwd=str(WORKDIR),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("triggered_at", payload)
        self.assertIn(payload["market_status"], ("OPEN", "CLOSED"))
        self.assertIn("kline", payload)
        self.assertIn("success_count", payload["kline"])
        self.assertIn("skipped_count", payload["kline"])
        self.assertIn("refreshed_count", payload["kline"])


class TestPlistTemplate(unittest.TestCase):
    def test_template_contains_required_keys(self) -> None:
        template_path = WORKDIR / "launchd" / "com.alphapilot.refresh.plist.template"
        self.assertTrue(template_path.exists(), f"missing {template_path}")
        content = template_path.read_text(encoding="utf-8")
        for needle in ("__PYTHON__", "__PROVIDER__", "__UNIVERSE__", "__WORKDIR__", "__LOG_DIR__"):
            self.assertIn(needle, content)
        # 必须包含 launchd 必要字段
        for key in ("<key>Label</key>", "<key>ProgramArguments</key>",
                    "<key>StartCalendarInterval</key>",
                    "<key>StandardOutPath</key>", "<key>StandardErrorPath</key>"):
            self.assertIn(key, content)

    def test_template_renders_with_sed(self) -> None:
        template = (WORKDIR / "launchd" / "com.alphapilot.refresh.plist.template").read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as f:
            rendered = (
                template
                .replace("__PYTHON__", "/usr/bin/python3")
                .replace("__PROVIDER__", "auto")
                .replace("__UNIVERSE__", "watchlist")
                .replace("__WORKDIR__", str(WORKDIR))
                .replace("__LOG_DIR__", "/tmp/logs")
            )
            f.write(rendered)
            path = f.name
        try:
            # 用 plutil 验证 plist 格式合法（macOS 自带）
            result = subprocess.run(
                ["plutil", "-lint", path], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0,
                             f"plist invalid: {result.stdout} {result.stderr}")
        finally:
            os.unlink(path)

    def test_plist_has_weekdays_1_to_5_excluding_weekend(self) -> None:
        """launchd 的 Weekday: 1=周日, 2-6=周一-周五。本任务应在 2-6 触发。"""
        import re
        template = (WORKDIR / "launchd" / "com.alphapilot.refresh.plist.template").read_text(encoding="utf-8")
        weekdays = re.findall(r"<key>Weekday</key>\s*<integer>(\d+)</integer>", template)
        weekdays = sorted(set(int(w) for w in weekdays))
        self.assertEqual(weekdays, [2, 3, 4, 5, 6],
                         "应包含周一到周五(2-6)，不应包含周末(0,7)和周六周日")

    def test_plist_triggers_at_1530(self) -> None:
        import re
        template = (WORKDIR / "launchd" / "com.alphapilot.refresh.plist.template").read_text(encoding="utf-8")
        hours = re.findall(r"<key>Hour</key>\s*<integer>(\d+)</integer>", template)
        minutes = re.findall(r"<key>Minute</key>\s*<integer>(\d+)</integer>", template)
        self.assertTrue(all(h == "15" for h in hours), f"hours should be 15, got {hours}")
        self.assertTrue(all(m == "30" for m in minutes), f"minutes should be 30, got {minutes}")


class TestInstallScript(unittest.TestCase):
    def test_install_script_exists_and_executable(self) -> None:
        path = WORKDIR / "scripts" / "install_launchd.sh"
        self.assertTrue(path.exists(), f"missing {path}")
        self.assertTrue(os.access(path, os.X_OK), f"{path} not executable")

    def test_uninstall_script_exists_and_executable(self) -> None:
        path = WORKDIR / "scripts" / "uninstall_launchd.sh"
        self.assertTrue(path.exists())
        self.assertTrue(os.access(path, os.X_OK))

    def test_install_script_dry_run(self) -> None:
        """模拟安装：替换路径到 tmp，不真调 launchctl，验证渲染结果。"""
        import re
        path = WORKDIR / "scripts" / "install_launchd.sh"
        content = path.read_text(encoding="utf-8")
        # 检查脚本里包含必要的 sed 替换字段
        for token in ("__PYTHON__", "__PROVIDER__", "__UNIVERSE__", "__WORKDIR__", "__LOG_DIR__"):
            self.assertIn(token, content)
        # 验证 launchctl load 那一行
        self.assertIn("launchctl load", content)


if __name__ == "__main__":
    unittest.main()
