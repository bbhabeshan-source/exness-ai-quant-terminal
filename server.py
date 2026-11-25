"""
Web Server for Exness AI Automated Trading Platform.
Serves the live web dashboard and JSON API for chart analysis, news scraping,
strategy selection, pair ranking, backtesting, and Exness MT5 / Paper execution.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
import sys
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import DEFAULT_PAIRS, SUPPORTED_TIMEFRAMES, TradingSettings
from database import Database
from engine.broker import ExnessBrokerManager
from engine.chart_analyzer import ChartAnalyzer
from engine.pipeline import TradingPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("WebServer")

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DB_PATH = os.path.join(BASE_DIR, "data", "exness_trader.db")

db = Database(DB_PATH)
settings = TradingSettings()
broker = ExnessBrokerManager(db, settings)
pipeline = TradingPipeline(db, settings, broker)


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # Keep console logs clean
        pass

    def _serve_static(self, rel_path: str) -> None:
        clean = rel_path.lstrip("/") or "index.html"
        full_path = os.path.abspath(os.path.join(FRONTEND_DIR, clean))
        frontend_abs = os.path.abspath(FRONTEND_DIR)
        if not full_path.startswith(frontend_abs) or not os.path.isfile(full_path):
            self.send_error(404, "File not found")
            return
        mime, _ = mimetypes.guess_type(full_path)
        with open(full_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json({"ok": True, "service": "Exness AI Automated Trader"})
            return

        if path == "/api/state":
            snap = pipeline.get_latest_snapshot()
            # Keep displayed prices fresh from live_feed/MT5 cache without blocking
            evals = snap.get("evaluations", {})
            for sym in evals:
                evals[sym]["tick"] = broker.get_tick(sym)
            self._send_json(
                {
                    "account": broker.get_account_summary(),
                    "settings": settings.to_public_dict(),
                    "timeframes": SUPPORTED_TIMEFRAMES,
                    "mt5_discovery": broker.get_available_servers(),
                    "pairs": {k: {"display_name": v.display_name, "category": v.category} for k, v in DEFAULT_PAIRS.items()},
                    "pipeline": snap,
                    "open_trades": db.list_trades(status="OPEN", limit=50),
                    "trade_history": db.list_trades(limit=50),
                    "recent_runs": db.list_pipeline_runs(limit=12),
                }
            )
            return

        if path.startswith("/api/tick/"):
            sym = path.split("/api/tick/")[-1].upper()
            if sym not in DEFAULT_PAIRS:
                self._send_json({"error": f"Unknown symbol {sym}"}, status=404)
                return
            if not broker.is_mt5_ready():
                broker.live_feed.refresh_quotes()
            tick = broker.get_tick(sym)
            self._send_json({"symbol": sym, "tick": tick})
            return

        if path.startswith("/api/chart/"):
            sym = path.split("/api/chart/")[-1].upper()
            if sym not in DEFAULT_PAIRS:
                self._send_json({"error": f"Unknown symbol {sym}"}, status=404)
                return
            qs = parse_qs(parsed.query)
            tf = (qs.get("tf", [settings.timeframe])[0] or settings.timeframe).upper()
            if tf not in SUPPORTED_TIMEFRAMES:
                tf = settings.timeframe
            candles = broker.get_candles(sym, timeframe=tf, count=350)
            tick = broker.get_tick(sym)
            chart = ChartAnalyzer.analyze(candles, sym, DEFAULT_PAIRS[sym].pip_size)
            self._send_json({"symbol": sym, "timeframe": tf, "tick": tick, "chart": chart})
            return

        # Serve static frontend files
        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json_body()
        except Exception:
            self._send_json({"error": "Invalid JSON body"}, status=400)
            return

        if path == "/api/run-pipeline":
            allow_exec = bool(body.get("execute", False))
            force_news = bool(body.get("refresh_news", True))
            if body.get("enable_autopilot"):
                settings.auto_trade_enabled = True
            snap = pipeline.run_cycle(allow_execute=allow_exec, force_news_refresh=force_news)
            self._send_json(
                {
                    "ok": True,
                    "account": broker.get_account_summary(),
                    "settings": settings.to_public_dict(),
                    "pipeline": snap,
                    "open_trades": db.list_trades(status="OPEN", limit=50),
                    "trade_history": db.list_trades(limit=50),
                    "recent_runs": db.list_pipeline_runs(limit=12),
                }
            )
            return

        if path == "/api/auto-trade/start":
            settings.auto_trade_enabled = True
            snap = pipeline.run_cycle(allow_execute=True, force_news_refresh=bool(body.get("refresh_news", True)))
            self._send_json(
                {
                    "ok": True,
                    "account": broker.get_account_summary(),
                    "settings": settings.to_public_dict(),
                    "pipeline": snap,
                    "open_trades": db.list_trades(status="OPEN", limit=50),
                    "trade_history": db.list_trades(limit=50),
                    "recent_runs": db.list_pipeline_runs(limit=12),
                }
            )
            return

        if path == "/api/auto-trade/stop":
            settings.auto_trade_enabled = False
            self._send_json(
                {
                    "ok": True,
                    "account": broker.get_account_summary(),
                    "settings": settings.to_public_dict(),
                }
            )
            return

        if path == "/api/settings":
            if "execution_mode" in body and body["execution_mode"] in ("PAPER", "EXNESS_MT5"):
                settings.execution_mode = body["execution_mode"]
            if "account_type" in body and body["account_type"] in ("DEMO", "LIVE"):
                settings.account_type = body["account_type"]
            if "live_confirmed" in body:
                settings.live_confirmed = bool(body["live_confirmed"])
            if "auto_trade_enabled" in body:
                settings.auto_trade_enabled = bool(body["auto_trade_enabled"])
            if "timeframe" in body and body["timeframe"] in SUPPORTED_TIMEFRAMES:
                settings.timeframe = body["timeframe"]
            if "lot_size" in body:
                settings.lot_size = max(0.01, min(10.0, float(body["lot_size"])))
            if "min_win_rate_gate" in body:
                settings.min_win_rate_gate = max(50.0, min(99.0, float(body["min_win_rate_gate"])))
            if "enforce_win_rate_gate" in body:
                settings.enforce_win_rate_gate = bool(body["enforce_win_rate_gate"])
            if "news_filter_enabled" in body:
                settings.news_filter_enabled = bool(body["news_filter_enabled"])
            if "max_concurrent_trades" in body:
                settings.max_concurrent_trades = max(1, min(10, int(body["max_concurrent_trades"])))
            if "enabled_pairs" in body and isinstance(body["enabled_pairs"], list):
                valid = [p for p in body["enabled_pairs"] if p in DEFAULT_PAIRS]
                if valid:
                    settings.enabled_pairs = valid

            self._send_json(
                {
                    "ok": True,
                    "settings": settings.to_public_dict(),
                    "account": broker.get_account_summary(),
                }
            )
            return

        if path == "/api/connect-mt5":
            if body.get("login"):
                settings.mt5_login = int(body["login"])
            if body.get("password"):
                settings.mt5_password = str(body["password"])
            if body.get("server"):
                settings.mt5_server = str(body["server"])
            if body.get("path"):
                settings.mt5_path = str(body["path"])
            res = broker.connect_mt5()
            if res.get("connected"):
                settings.execution_mode = "EXNESS_MT5"
                settings.account_type = res.get("trade_mode", "DEMO")
            self._send_json(
                {
                    "ok": bool(res.get("connected")),
                    "mt5": res,
                    "settings": settings.to_public_dict(),
                    "account": broker.get_account_summary(),
                }
            )
            return

        if path == "/api/disconnect-mt5":
            broker.disconnect_mt5()
            settings.execution_mode = "PAPER"
            settings.auto_trade_enabled = False
            self._send_json(
                {
                    "ok": True,
                    "settings": settings.to_public_dict(),
                    "account": broker.get_account_summary(),
                }
            )
            return

        if path == "/api/execute-manual":
            sym = str(body.get("symbol", "XAUUSD")).upper()
            direction = str(body.get("direction", "BUY")).upper()
            if sym not in DEFAULT_PAIRS or direction not in ("BUY", "SELL"):
                self._send_json({"ok": False, "error": "Invalid symbol or direction"}, status=400)
                return
            snap = pipeline.get_latest_snapshot()
            ev = snap.get("evaluations", {}).get(sym, {})
            strat = ev.get("strategy", {})
            spec = DEFAULT_PAIRS[sym]
            res = broker.execute_trade(
                canonical=sym,
                direction=direction,
                volume=float(body.get("lot_size", settings.lot_size)),
                sl_pips=float(strat.get("sl_pips", spec.default_sl_pips)),
                tp_pips=float(strat.get("tp_pips", spec.default_tp_pips)),
                strategy=str(strat.get("best_strategy", "MANUAL_OVERRIDE")),
                confidence=float(strat.get("confidence", 75.0)),
                backtest_win_rate=float(strat.get("backtest_win_rate", 0.0)),
                news_bias=str(strat.get("news_bias", "NEUTRAL")),
            )
            self._send_json(
                {
                    "ok": bool(res.get("success")),
                    "result": res,
                    "account": broker.get_account_summary(),
                    "open_trades": db.list_trades(status="OPEN", limit=50),
                    "trade_history": db.list_trades(limit=50),
                }
            )
            return

        if path == "/api/close-trade":
            ticket = body.get("ticket")
            if ticket is None:
                self._send_json({"ok": False, "error": "Missing ticket"}, status=400)
                return
            res = broker.close_position_by_ticket(int(ticket))
            self._send_json(
                {
                    "ok": bool(res.get("success")),
                    "result": res,
                    "account": broker.get_account_summary(),
                    "open_trades": db.list_trades(status="OPEN", limit=50),
                    "trade_history": db.list_trades(limit=50),
                }
            )
            return

        self._send_json({"error": "Endpoint not found"}, status=404)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def run_server(host: str = "127.0.0.1", port: int = 8899) -> None:
    pipeline.start_background_loop()
    # Warm up initial snapshot so first UI load is instant
    pipeline.run_cycle(allow_execute=False, force_news_refresh=False)
    try:
        server = ExclusiveHTTPServer((host, port), AppHandler)
    except OSError:
        server = ExclusiveHTTPServer((host, 0), AppHandler)
    bound_port = server.server_address[1]
    print(f"Exness AI Trader Web Dashboard running at http://{host}:{bound_port}", flush=True)
    logger.info(f"Exness AI Trader Web Dashboard running at http://{host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pipeline.stop_background_loop()
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8899"))
    run_server(port=port)
