from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Optional

from alphapilot.server import AlphaPilotHandler
from alphapilot.service import AlphaPilotService


class AlphaPilotServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.service = AlphaPilotService(Path(self.tmp.name) / "test.sqlite")
        self.service.initialize_data(provider="sample", universe="watchlist", years=1)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- 仪表盘和状态 --------------------------------------------------

    def test_dashboard_endpoint(self) -> None:
        result = _call_handler(self.service, "GET", "/api/dashboard")
        self.assertIn("data_status", result)
        self.assertIn("signals", result)
        self.assertIn("benchmarks", result)

    def test_status_endpoint(self) -> None:
        result = _call_handler(self.service, "GET", "/api/status")
        self.assertIn("provider_mix", result)
        self.assertIn("fund_flow", result)
        self.assertIn("symbol_sources", result)

    def test_universes_endpoint(self) -> None:
        result = _call_handler(self.service, "GET", "/api/signal-universes")
        self.assertIn("universes", result)
        values = [u["value"] for u in result["universes"]]
        self.assertIn("watchlist", values)
        self.assertIn("all_a", values)

    def test_config_endpoint_get(self) -> None:
        result = _call_handler(self.service, "GET", "/api/config")
        self.assertIn("settings", result)

    def test_config_endpoint_post(self) -> None:
        result = _call_handler(
            self.service,
            "POST",
            "/api/config",
            {"require_market_above_ma20": False, "cooldown_loss_count": 4},
        )
        self.assertIn("settings", result)
        self.assertFalse(result["settings"]["require_market_above_ma20"])
        self.assertEqual(result["settings"]["cooldown_loss_count"], 4)

    # --- 信号 --------------------------------------------------------

    def test_signals_with_limit(self) -> None:
        result = _call_handler(self.service, "GET", "/api/signals?universe=all_a&limit=3")
        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 3)

    def test_signals_paginated(self) -> None:
        result = _call_handler(
            self.service, "GET", "/api/signals?universe=all_a&page=1&page_size=20"
        )
        self.assertIn("rows", result)
        self.assertEqual(result["page_size"], 20)
        self.assertGreaterEqual(result["page"], 1)

    def test_signals_paginated_respects_max_page_size(self) -> None:
        # page_size > MAX_SIGNAL_LIMIT 应被夹逼
        result = _call_handler(
            self.service, "GET", "/api/signals?universe=all_a&page=1&page_size=9999"
        )
        self.assertLessEqual(result["page_size"], 1000)

    def test_signals_default_universe(self) -> None:
        # 不指定 universe，应默认为 watchlist
        result = _call_handler(self.service, "GET", "/api/signals")
        self.assertIsInstance(result, list)

    # --- 交易记录 ----------------------------------------------------

    def test_mark_endpoint(self) -> None:
        result = _call_handler(
            self.service,
            "POST",
            "/api/mark",
            {"code": "300124.SZ", "side": "BUY", "shares": 100, "note": "测试买入"},
        )
        self.assertEqual(result["side"], "BUY")
        self.assertEqual(result["shares"], 100)
        # 持仓应更新
        self.assertEqual(self.service.journal.holdings()["300124.SZ"]["shares"], 100)

    def test_journal_endpoint(self) -> None:
        _call_handler(
            self.service,
            "POST",
            "/api/mark",
            {"code": "300124.SZ", "side": "BUY", "shares": 100, "note": "测试"},
        )
        result = _call_handler(self.service, "GET", "/api/journal")
        self.assertIn("marks", result)
        self.assertEqual(len(result["marks"]), 1)
        self.assertEqual(result["marks"][0]["code"], "300124.SZ")

    def test_mark_invalid_side_returns_400(self) -> None:
        result = _call_with_status(
            self.service,
            "POST",
            "/api/mark",
            {"code": "300124.SZ", "side": "HOLD", "shares": 100},
        )
        self.assertEqual(result["status"], 400)
        self.assertIn("error", result["body"])

    def test_mark_missing_field_returns_400(self) -> None:
        result = _call_with_status(
            self.service,
            "POST",
            "/api/mark",
            {"code": "300124.SZ", "side": "BUY"},  # 缺 shares
        )
        self.assertEqual(result["status"], 400)

    def test_config_reset_endpoint_restores_defaults(self) -> None:
        """POST /api/config/reset 必须能清掉 DB 中保存的 settings。"""
        # 先污染 settings
        self.service.update_strategy_config({
            "require_market_above_ma20": False,
            "allow_normal_position": False,
            "enable_loss_streak_cooldown": False,
        })
        polluted = self.service.strategy_config()["settings"]
        self.assertFalse(polluted["allow_normal_position"])

        # 调 reset 端点
        result = _call_handler(self.service, "POST", "/api/config/reset")
        self.assertTrue(result["reset"])
        self.assertEqual(result["settings"], result["defaults"])
        # 当前 service 实例也应看到默认
        self.assertTrue(self.service.strategy_config()["settings"]["allow_normal_position"])

    # --- 错误处理 ----------------------------------------------------

    def test_unknown_route_returns_404(self) -> None:
        result = _raw_response(self.service, "GET", "/api/nonexistent")
        self.assertEqual(result["status"], 404)

    def test_post_unknown_route_returns_404(self) -> None:
        result = _raw_response(
            self.service, "POST", "/api/bogus", {"foo": "bar"}
        )
        self.assertEqual(result["status"], 404)

    def test_config_invalid_setting_clamps(self) -> None:
        # cooldown_loss_count=999 会被 Trend20Settings.__post_init__ 夹逼到 10
        result = _call_handler(
            self.service,
            "POST",
            "/api/config",
            {"cooldown_loss_count": 999},
        )
        self.assertEqual(result["settings"]["cooldown_loss_count"], 10)


# ---------------------------------------------------------------------------
# 辅助函数（不再需要 monkey-patch 类变量）
# ---------------------------------------------------------------------------


def _make_handler(service: AlphaPilotService, method: str, path: str, body: bytes) -> AlphaPilotHandler:
    handler = AlphaPilotHandler.__new__(AlphaPilotHandler)
    handler.set_service(service)
    handler.command = method
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.server = object()
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.headers = {"content-length": str(len(body)), "content-type": "application/json"}
    return handler


def _call_handler(service: AlphaPilotService, method: str, path: str, payload: Optional[dict] = None) -> dict:
    result = _call_with_status(service, method, path, payload)
    return result["body"]


def _call_with_status(service: AlphaPilotService, method: str, path: str, payload: Optional[dict] = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
    handler = _make_handler(service, method, path, body)
    if method == "GET":
        handler.do_GET()
    elif method == "POST":
        handler.do_POST()
    else:
        raise AssertionError(f"unsupported method: {method}")
    raw = handler.wfile.getvalue()
    head, _, body_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii")
    status_code = int(status_line.split(" ")[1])
    # 尝试解析 JSON，失败时返回原始字节（404 通常走 HTML 路径）
    try:
        body_obj = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        body_obj = None
    return {"status": status_code, "body": body_obj}


def _raw_response(service: AlphaPilotService, method: str, path: str, payload: Optional[dict] = None) -> dict:
    """返回完整 HTTP 响应（含状态码和原始 body），用于 404 等非 JSON 场景。"""
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
    handler = _make_handler(service, method, path, body)
    if method == "GET":
        handler.do_GET()
    elif method == "POST":
        handler.do_POST()
    return {
        "status": int(handler.wfile.getvalue().split(b" ", 2)[1]),
        "raw": handler.wfile.getvalue(),
    }


if __name__ == "__main__":
    unittest.main()
