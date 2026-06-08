"""sector 切弱 fallback 行为单元测试 (2026-06-07)

覆盖 _hasActionable 工具函数(纯 JS,但我们用 Python 模拟)
注: 真正的端到端 fallback 验证在浏览器层做(Playwright)
"""
from __future__ import annotations

import unittest


# 模拟 JS 的 _hasActionable 函数(把 HTML 里的 JS 逻辑复刻)
def _hasActionable_py(grouped):
    for action in ("buy", "trial", "exit_alert", "stop"):
        if grouped.get(action) and len(grouped[action]) > 0:
            return True
    return False


class HasActionablePythonicTest(unittest.TestCase):
    """Python 镜像 _hasActionable JS 逻辑(确认行为正确)"""

    def test_empty_grouped(self) -> None:
        self.assertFalse(_hasActionable_py({}))

    def test_only_skip(self) -> None:
        self.assertFalse(_hasActionable_py({"skip": [{"code": "X"}]}))

    def test_buy(self) -> None:
        self.assertTrue(_hasActionable_py({"buy": [{"code": "X"}]}))

    def test_trial(self) -> None:
        self.assertTrue(_hasActionable_py({"trial": [{"code": "X"}]}))

    def test_exit_alert(self) -> None:
        self.assertTrue(_hasActionable_py({"exit_alert": [{"code": "X"}]}))

    def test_stop(self) -> None:
        self.assertTrue(_hasActionable_py({"stop": [{"code": "X"}]}))

    def test_multiple_buy(self) -> None:
        self.assertTrue(_hasActionable_py({"buy": [{"code": "X"}, {"code": "Y"}]}))


class FallbackLogicTest(unittest.TestCase):
    """fallback 决策逻辑(纯 Python 镜像 JS 控制流)"""

    def test_should_fallback_when_new_empty_and_prev_has_actionable(self) -> None:
        """新 sector 没 actionable,老 cache 有 → fallback"""
        new_grouped = {"skip": [{"code": "X"}]}  # 新 sector 弱
        prev_signals = [{"code": "Y", "action": "NORMAL"}]  # 老 cache 有 actionable
        should_fallback = (
            not _hasActionable_py(new_grouped)
            and prev_signals
            and len(prev_signals) > 0
        )
        self.assertTrue(should_fallback)

    def test_no_fallback_when_new_has_actionable(self) -> None:
        """新 sector 有 actionable → 不用 fallback"""
        new_grouped = {"buy": [{"code": "X"}]}
        prev_signals = [{"code": "Y", "action": "NORMAL"}]
        should_fallback = (
            not _hasActionable_py(new_grouped)
            and prev_signals
            and len(prev_signals) > 0
        )
        self.assertFalse(should_fallback)

    def test_no_fallback_when_prev_cache_empty(self) -> None:
        """新 sector 弱,老 cache 是空(冷启第一次切) → 不 fallback"""
        new_grouped = {"skip": [{"code": "X"}]}
        prev_signals = []  # 冷启第一次切
        should_fallback = (
            not _hasActionable_py(new_grouped)
            and prev_signals
            and len(prev_signals) > 0
        )
        self.assertFalse(should_fallback, "冷启首次切 sector 时老 cache 空,不应 fallback")


if __name__ == "__main__":
    unittest.main()
