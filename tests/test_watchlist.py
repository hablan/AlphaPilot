"""测试自选股功能。"""
from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from alphapilot.data.cache import MarketDataCache
from alphapilot.journal.store import JournalStore
from alphapilot.service import AlphaPilotService
from alphapilot.config import WATCHLIST


class TestWatchlistCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_watchlist(self) -> None:
        self.assertEqual(self.cache.list_watchlist(), [])

    def test_add_and_list(self) -> None:
        self.assertTrue(self.cache.add_to_watchlist("000001.SZ", "平安银行", sector="银行"))
        wl = self.cache.list_watchlist()
        self.assertEqual(len(wl), 1)
        self.assertEqual(wl[0]["symbol"], "000001.SZ")
        self.assertEqual(wl[0]["name"], "平安银行")
        self.assertEqual(wl[0]["sector"], "银行")

    def test_add_duplicate_returns_false(self) -> None:
        self.assertTrue(self.cache.add_to_watchlist("000001.SZ", "A"))
        self.assertFalse(self.cache.add_to_watchlist("000001.SZ", "A"))  # 重复

    def test_remove(self) -> None:
        self.cache.add_to_watchlist("000001.SZ", "A")
        self.assertTrue(self.cache.remove_from_watchlist("000001.SZ"))
        self.assertFalse(self.cache.remove_from_watchlist("000001.SZ"))  # 已删
        self.assertEqual(self.cache.list_watchlist(), [])


class TestWatchlistService(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.cache = MarketDataCache(self.db)
        # 注入 watchlist 7 个
        for inst in WATCHLIST:
            self.cache.upsert_instrument(inst.symbol, inst.name, inst.asset_type, inst.sector)
        self.service = AlphaPilotService(self.db)
        self.service.cache = self.cache
        self.service.journal = JournalStore(self.cache)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_signal_instruments_watchlist_merges_user_picks(self) -> None:
        # 初始：只有 WATCHLIST 7 个
        instruments = self.service._signal_instruments("watchlist")
        self.assertEqual(len(instruments), 7)

        # 加自选 2 个
        self.service.add_to_watchlist("000001.SZ", "平安银行", "银行")
        self.service.add_to_watchlist("000002.SZ", "万科 A", "地产")
        instruments = self.service._signal_instruments("watchlist")
        self.assertEqual(len(instruments), 9)

    def test_signal_universes_count_reflects_user_picks(self) -> None:
        universes = {u["value"]: u for u in self.service.signal_universes()}
        self.assertEqual(universes["watchlist"]["count"], 7)
        # 加 2 个新自选
        self.service.add_to_watchlist("000001.SZ", "A", "X")
        self.service.add_to_watchlist("000002.SZ", "B", "Y")
        universes = {u["value"]: u for u in self.service.signal_universes()}
        self.assertEqual(universes["watchlist"]["count"], 9)

    def test_watchlist_endpoint_returns_user_picks(self) -> None:
        self.service.add_to_watchlist("000001.SZ", "平安银行")
        wl = self.service.watchlist()
        self.assertEqual(len(wl), 1)
        self.assertEqual(wl[0]["symbol"], "000001.SZ")


class TestWatchlistHTTP(unittest.TestCase):
    """HTTP 层 smoke test：用 set_service() 注入 fake service 验证路由走通。"""

    def _make_handler(self, service, path, body=None):
        from alphapilot.server import AlphaPilotHandler
        handler = AlphaPilotHandler.__new__(AlphaPilotHandler)
        handler.set_service(service)
        handler.command = "POST" if body is not None else "GET"
        handler.path = path
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{handler.command} {path} HTTP/1.1"
        handler.client_address = ("127.0.0.1", 0)
        handler.server = object()
        handler.rfile = BytesIO(body or b"")
        handler.wfile = BytesIO()
        handler.headers = {"content-length": str(len(body or b""))}
        if body is not None:
            handler.do_POST()
        else:
            handler.do_GET()
        raw = handler.wfile.getvalue()
        head, _, body_bytes = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        return status, payload

    def test_add_remove_list_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from alphapilot.data.cache import MarketDataCache
            cache = MarketDataCache(Path(tmp) / "test.db")
            service = AlphaPilotService(Path(tmp) / "test.db")
            service.cache = cache

            # add
            payload = json.dumps({"code": "000001.SZ", "name": "平安银行"}).encode()
            status, body = self._make_handler(service, "/api/watchlist/add", payload)
            self.assertEqual(status, 200)
            self.assertTrue(body["added"])

            # list (GET)
            status, body = self._make_handler(service, "/api/watchlist")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["watchlist"]), 1)

            # remove
            payload = json.dumps({"code": "000001.SZ"}).encode()
            status, body = self._make_handler(service, "/api/watchlist/remove", payload)
            self.assertEqual(status, 200)
            self.assertTrue(body["removed"])


if __name__ == "__main__":
    unittest.main()
