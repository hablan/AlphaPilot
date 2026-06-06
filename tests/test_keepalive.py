"""测试 healthcheck 端点 + serve plist 模板。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKDIR = Path("/Users/henry/Applications/AlphaPilot")


class TestHealthzEndpoint(unittest.TestCase):
    def _call_handler(self, service, method, path):
        from io import BytesIO
        from alphapilot.server import AlphaPilotHandler
        handler = AlphaPilotHandler.__new__(AlphaPilotHandler)
        handler.set_service(service)
        handler.command = method
        handler.path = path
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.client_address = ("127.0.0.1", 0)
        handler.server = object()
        handler.rfile = BytesIO(b"")
        handler.wfile = BytesIO()
        handler.headers = {"content-length": "0"}
        handler.do_GET() if method == "GET" else handler.do_POST()
        raw = handler.wfile.getvalue()
        head, _, body_bytes = raw.partition(b"\r\n\r\n")
        status_code = int(head.split(b" ", 2)[1])
        try:
            body = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        return {"status": status_code, "body": body}

    def test_healthz_returns_ok(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from alphapilot.service import AlphaPilotService
            service = AlphaPilotService(Path(tmp) / "test.db")
            result = self._call_handler(service, "GET", "/healthz")
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["body"]["status"], "ok")

    def test_healthz_handles_db_failure(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from alphapilot.service import AlphaPilotService
            service = AlphaPilotService(Path(tmp) / "test.db")
            with patch.object(service, "cache_status", side_effect=RuntimeError("db locked")):
                result = self._call_handler(service, "GET", "/healthz")
            self.assertEqual(result["status"], 503)
            self.assertEqual(result["body"]["status"], "degraded")


class TestServePlistTemplate(unittest.TestCase):
    def test_template_exists_with_keepalive(self) -> None:
        template_path = WORKDIR / "launchd" / "com.alphapilot.serve.plist.template"
        self.assertTrue(os.path.exists(template_path))
        content = Path(template_path).read_text(encoding="utf-8")
        # 关键字段
        for needle in ("__PYTHON__", "__HOST__", "__PORT__", "__WORKDIR__", "__LOG_DIR__"):
            self.assertIn(needle, content)
        self.assertIn("RunAtLoad", content)
        self.assertIn("KeepAlive", content)
        self.assertIn("com.alphapilot.serve", content)

    def test_template_renders_legally(self) -> None:
        template = (WORKDIR / "launchd" / "com.alphapilot.serve.plist.template").read_text(encoding="utf-8")
        rendered = (template
            .replace("__PYTHON__", "/usr/bin/python3")
            .replace("__HOST__", "127.0.0.1")
            .replace("__PORT__", "8765")
            .replace("__WORKDIR__", str(WORKDIR))
            .replace("__LOG_DIR__", "/tmp/alphapilot-logs"))
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as f:
            f.write(rendered)
            path = f.name
        try:
            result = subprocess.run(
                ["plutil", "-lint", path], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0,
                             f"plist invalid: {result.stdout} {result.stderr}")
        finally:
            os.unlink(path)


class TestInstallServeScript(unittest.TestCase):
    def test_script_exists_and_executable(self) -> None:
        path = WORKDIR / "scripts" / "install_serve_launchd.sh"
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.access(path, os.X_OK))


if __name__ == "__main__":
    unittest.main()
