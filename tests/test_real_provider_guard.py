"""测试真实数据源保护（拒绝 sample 误用）。"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

WORKDIR = "/Users/henry/Applications/AlphaPilot"


class TestRealProviderGuard(unittest.TestCase):
    def test_sample_provider_in_cli_blocks_without_env(self) -> None:
        """未设置 ALLOW_SAMPLE_DATA 时，init-data --provider sample 应退出码 2。"""
        env = {k: v for k, v in os.environ.items() if k != "ALLOW_SAMPLE_DATA"}
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "init-data",
             "--provider", "sample", "--universe", "watchlist",
             "--max-symbols", "1"],
            capture_output=True,
            text=True,
            env=env,
            cwd=WORKDIR,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("sample 是确定性假数据", result.stderr)

    def test_sample_provider_allowed_with_env(self) -> None:
        """设置 ALLOW_SAMPLE_DATA=1 时应能跑。"""
        env = {k: v for k, v in os.environ.items() if k != "ALLOW_SAMPLE_DATA"}
        env["ALLOW_SAMPLE_DATA"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "init-data",
             "--provider", "sample", "--universe", "watchlist",
             "--max-symbols", "1"],
            capture_output=True,
            text=True,
            env=env,
            cwd=WORKDIR,
        )
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}\nstdout: {result.stdout}")

    def test_refresh_blocks_sample(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ALLOW_SAMPLE_DATA"}
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "refresh",
             "--provider", "sample", "--universe", "watchlist"],
            capture_output=True,
            text=True,
            env=env,
            cwd=WORKDIR,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("已被禁用", result.stderr)

    def test_refresh_passes_with_auto(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ALLOW_SAMPLE_DATA"}
        result = subprocess.run(
            [sys.executable, "-m", "alphapilot.cli", "refresh",
             "--provider", "auto", "--universe", "watchlist"],
            capture_output=True,
            text=True,
            env=env,
            cwd=WORKDIR,
        )
        # auto 是真实源，应能跑（不论成败）。关注 returncode
        self.assertNotEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
