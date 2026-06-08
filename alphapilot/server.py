from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from alphapilot.config import SERVER_HOST, SERVER_PORT
from alphapilot.i18n import HTTP_ERR_BAD_PARAMS, HTTP_ERR_INTERNAL, HTTP_ERR_SERVICE_UNAVAILABLE
from alphapilot.service import AlphaPilotService


WEB_DIR = Path(__file__).resolve().parent / "web"


# 启动时计算 BUILD_ID：基于 index.html 的 mtime + 启动时间。
# 每次服务启动都会变化，前端在 init 时检查 /api/build-id，发现变化就自动 reload 强制刷新。
_INDEX_PATH = WEB_DIR / "index.html"
_BUILD_ID = hashlib.sha256(
    f"{_INDEX_PATH.stat().st_mtime_ns}-{os.getpid()}-{int(time.time())}".encode()
).hexdigest()[:12]
_BUILD_ID_LOCK = threading.Lock()


class AlphaPilotHandler(BaseHTTPRequestHandler):
    # 类级 service 作为默认值；实例级 service 可通过 set_service() 注入，便于测试和并行调用
    service: AlphaPilotService = AlphaPilotService()
    # 2026-06-07: 注入 akshare provider,让 /api/quote 在盘中返回 intraday 实时价
    try:
        service.set_provider("akshare")
    except Exception:
        # provider 加载失败(无 akshare 库/网络问题)时 quote 仍可走 fallback 日线
        pass

    def set_service(self, service: AlphaPilotService) -> None:
        self.service = service

    @staticmethod
    def _compute_etag(dashboard: dict) -> str:
        """2026-06-07: dashboard 的 ETag,基于 as_of + bar_count + market_state + sector_state。
        任何一个变化就视为有新数据,需重发。
        """
        try:
            as_of = dashboard.get("as_of", "")
            ds = dashboard.get("data_status") or {}
            bar_count = ds.get("bar_count", 0)
            latest = ds.get("latest_trade_date") or ""
            metrics = dashboard.get("metrics") or {}
            market_state = metrics.get("market_state", "")
            # sector_state 可能在 metrics 或 benchmarks 里
            sector_state = metrics.get("sector_state", "")
            if not sector_state:
                benchmarks = dashboard.get("benchmarks") or []
                for b in benchmarks:
                    if b.get("key") == "sector":
                        sector_state = b.get("state", "")
                        break
            payload = f"{as_of}|{bar_count}|{latest}|{market_state}|{sector_state}"
            return hashlib.md5(payload.encode()).hexdigest()[:12]
        except Exception:
            # ETag 算不出时给个稳态,不影响 200 路径
            return "fallback"

    def end_headers(self) -> None:
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")
        # 防止浏览器缓存 HTML，迭代 Web UI 时总是拿到最新版
        self.send_header("cache-control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("pragma", "no-cache")
        self.send_header("expires", "0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/healthz":
            # 健康检查：返回服务存活状态 + 数据库连通
            try:
                cache_status = self.service.cache_status()
                self._send_json({
                    "status": "ok",
                    "as_of": cache_status.get("latest_trade_date"),
                    "symbols": cache_status.get("symbol_count"),
                })
            except Exception as exc:
                self._send_json({"status": "degraded", "error": str(exc)}, status=503)
            return
        if parsed.path == "/api/build-id":
            # 前端用于检测是否需要硬刷新（解决浏览器缓存导致看到旧 UI 的问题）
            self._send_json({"build_id": _BUILD_ID})
            return
        if parsed.path == "/api/dashboard":
            # 2026-06-07: ETag 协商,数据未变就 304,真正 0 流量
            dashboard = self.service.dashboard()
            etag = self._compute_etag(dashboard)
            client_etag = self.headers.get("If-None-Match", "").strip('"')
            if client_etag and client_etag == etag:
                # 304 响应需要 ETag header
                self._send_json({"status": "not_modified"}, status=304, extra_headers={"etag": etag})
                return
            self._send_json(dashboard, extra_headers={"etag": etag})
            return
        if parsed.path == "/api/dashboard/summary":
            # 2026-06-07: 精简版,只含首屏 banner/3 指标/data_status
            # 不算 signals / sector_ranking / holding_risks / performance_curve
            # 典型耗时 100-300ms(对比 /api/dashboard 冷启 2.8s)
            self._send_json(self.service.dashboard_summary())
            return
        if parsed.path == "/api/paper/equity-curve":
            self._send_json(self.service.paper_equity_curve())
            return
        if parsed.path == "/api/signals":
            query = parse_qs(parsed.query)
            universe = (query.get("universe") or ["watchlist"])[0]
            page_text = (query.get("page") or [None])[0]
            page_size_text = (query.get("page_size") or query.get("limit") or [None])[0]
            market = (query.get("market") or [None])[0]
            style = (query.get("style") or [None])[0]
            sector = (query.get("sector") or [None])[0]
            keyword = (query.get("keyword") or [None])[0]
            if page_text is not None:
                page = int(page_text)
                page_size = int(page_size_text) if page_size_text else 20
                self._send_json(
                    self.service.signal_page(
                        universe=universe, page=page, page_size=page_size,
                        market=market, style=style, sector=sector, keyword=keyword,
                    )
                )
                return
            limit_text = (query.get("limit") or [None])[0]
            limit = int(limit_text) if limit_text else None
            self._send_json(
                self.service.signals(
                    universe=universe, limit=limit,
                    market=market, style=style, sector=sector, keyword=keyword,
                )
            )
            return
        if parsed.path == "/api/quote":
            query = parse_qs(parsed.query)
            code = (query.get("code") or [None])[0]
            if not code:
                self._send_json({"error": "missing code"}, status=400)
                return
            self._send_json(self.service.quote(code))
            return
        if parsed.path == "/api/benchmark-options":
            self._send_json(self.service.benchmark_options())
            return
        if parsed.path == "/api/watchlist":
            self._send_json({"watchlist": self.service.watchlist()})
            return
        if parsed.path == "/api/mark-instruments":
            # 全市场标的列表（供 mark 表单搜索/选择）
            self._send_json({"instruments": self.service.all_instruments()})
            return
        if parsed.path == "/api/signal-universes":
            self._send_json({"universes": self.service.signal_universes()})
            return
        if parsed.path == "/api/config/presets":
            self._send_json({"presets": self.service.list_presets()})
            return
        if parsed.path == "/api/backtest":
            self._send_json(self.service.backtest())
            return
        if parsed.path == "/api/journal":
            self._send_json({"marks": self.service.marks()})
            return
        if parsed.path == "/api/status":
            self._send_json(self.service.data_status())
            return
        if parsed.path == "/api/config":
            self._send_json(self.service.strategy_config())
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if parsed.path == "/api/config":
                self._send_json(self.service.update_strategy_config(payload))
                return
            if parsed.path == "/api/config/reset":
                # 一键恢复 Trend20Settings 到默认：手抖/测试误改的快速修复
                self._send_json(self.service.reset_strategy_config())
                return
            if parsed.path == "/api/config/apply-preset":
                name = payload.get("preset")
                self._send_json(self.service.apply_preset(name))
                return
            if parsed.path == "/api/refresh":
                # 增量更新：只拉 last_trade_date+1 到今天
                result = self.service.incremental_update(
                    provider=payload.get("provider", "auto"),
                    universe=payload.get("universe", "watchlist"),
                )
                # 2026-06-07: 刷新后清空 dashboard/backtest 缓存,让下次请求拿到新数据
                self.service._ttl_cache.invalidate()
                self._send_json(result)
                return
            if parsed.path == "/api/watchlist/add":
                self._send_json(self.service.add_to_watchlist(
                    symbol=payload["code"],
                    name=payload.get("name", payload["code"]),
                    sector=payload.get("sector", ""),
                ))
                return
            if parsed.path == "/api/watchlist/remove":
                self._send_json(self.service.remove_from_watchlist(symbol=payload["code"]))
                return
            if parsed.path != "/api/mark":
                self.send_error(404, "Not found")
                return
            result = self.service.mark_trade(
                payload["code"],
                payload["side"],
                int(payload["shares"]),
                price=float(payload["price"]) if payload.get("price") else None,
                note=payload.get("note"),
                mode=payload.get("mode", "real"),
            )
            # 2026-06-07: 标记交易后清空 dashboard 缓存,持仓/盈亏立即反映
            self.service._ttl_cache.invalidate("dashboard")
            self._send_json(result)
        except (ValueError, KeyError) as exc:
            self._send_json({"error": HTTP_ERR_BAD_PARAMS.format(exc=exc)}, status=400)
        except RuntimeError as exc:
            self._send_json({"error": HTTP_ERR_SERVICE_UNAVAILABLE.format(exc=exc)}, status=503)
        except Exception:
            self._send_json({"error": HTTP_ERR_INTERNAL}, status=500)

    def log_message(self, format: str, *args) -> None:
        # 临时打开日志以便调试；上线后可关
        import sys
        print(f"[HTTP] {self.command} {self.path}", file=sys.stderr)

    def _send_json(self, payload, status: int = 200, extra_headers: Optional[dict] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        # 对 HTML 注入 BUILD_ID：让前端能检测自己是否过期，强制硬刷新
        if path.name == "index.html" and content_type.startswith("text/html"):
            try:
                html = body.decode("utf-8")
                inject = f'<meta name="build-id" content="{_BUILD_ID}">'
                # 插在 </head> 之前；如果没有就插在 <body> 之前
                if "</head>" in html:
                    html = html.replace("</head>", f"  {inject}\n  </head>", 1)
                elif "<body" in html:
                    html = html.replace("<body", f"{inject}\n  <body", 1)
                body = html.encode("utf-8")
            except Exception:
                pass  # 注入失败也不影响返回原 HTML
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = SERVER_HOST, port: int = SERVER_PORT) -> None:
    server = ThreadingHTTPServer((host, port), AlphaPilotHandler)
    print(f"AlphaPilot MVP running at http://{host}:{port}")
    server.serve_forever()
