"""End-to-end verification for Exness AI Automated Trading Platform."""

from http.server import ThreadingHTTPServer
import json
import os
import sys
import tempfile
import threading
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import TradingSettings
from database import Database
from engine.broker import ExnessBrokerManager
from engine.pipeline import TradingPipeline
import server
from server import AppHandler


def run_tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        settings = TradingSettings()
        broker = ExnessBrokerManager(db, settings)
        test_pipe = TradingPipeline(db, settings, broker)

        snap = test_pipe.run_cycle(allow_execute=False, force_news_refresh=False)
        assert snap["selected_pair"] in settings.enabled_pairs, "Expected selected pair"
        assert "XAUUSD" in snap["evaluations"] and "GBPUSD" in snap["evaluations"], "Expected XAUUSD & GBPUSD"
        assert len(snap["rankings"]) == len(settings.enabled_pairs), "Expected all pairs ranked"
        print("PIPELINE_OK", snap["selected_pair"], snap["selected_strategy"], snap["backtest_win_rate"])

        res = broker.execute_trade(
            canonical="XAUUSD",
            direction="BUY",
            volume=0.01,
            sl_pips=12.0,
            tp_pips=18.0,
            strategy="EMA_PULLBACK_SNIPER",
            confidence=82.5,
            backtest_win_rate=87.5,
            news_bias="BUY",
        )
        assert res["success"], f"Paper trade failed: {res}"
        tkt = res["trade"]["ticket"]
        close_res = broker.close_position_by_ticket(tkt)
        assert close_res["success"], f"Paper close failed: {close_res}"
        stats = db.get_trade_stats()
        assert stats["total_closed"] == 1, "Expected 1 closed trade"
        print("EXECUTION_OK", tkt, stats)

    # Verify HTTP endpoints and frontend static serving in isolated temp DB
    with tempfile.TemporaryDirectory() as http_tmp:
        server.db = Database(os.path.join(http_tmp, "http_test.db"))
        server.settings = TradingSettings()
        server.broker = ExnessBrokerManager(server.db, server.settings)
        server.pipeline = TradingPipeline(server.db, server.settings, server.broker)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                assert json.loads(r.read().decode())["ok"] is True
                assert r.headers.get("Access-Control-Allow-Origin") == "*"
            assert os.path.isfile(os.path.join(BASE_DIR, "index.html"))
            assert os.path.isfile(os.path.join(BASE_DIR, "web_engine.js"))
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                html = r.read().decode()
                assert "Exness AI Quant Terminal" in html
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=20) as r:
                st = json.loads(r.read().decode())
                assert "pipeline" in st and "account" in st
                assert st["timeframes"] == ["M1", "M5", "M15", "M30", "H1", "H4"]
                assert len(st["mt5_discovery"]["servers"]) >= 70
                for sym in ["XAUUSD", "GBPUSD", "EURUSD", "USDJPY", "GBPJPY", "EURGBP"]:
                    tk = st["pipeline"]["evaluations"][sym]["tick"]
                    print("LIVE_TICK", sym, tk["mid"], tk["source"])
            for tf in ["M1", "M5", "M15", "M30", "H1", "H4"]:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/chart/XAUUSD?tf={tf}", timeout=15) as r:
                    ch = json.loads(r.read().decode())
                    assert ch["timeframe"] == tf and len(ch["chart"]["series"]) >= 60
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/vendor/lightweight-charts.js", timeout=5) as r:
                assert b"LightweightCharts" in r.read()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tick/XAUUSD", timeout=10) as r:
                tk = json.loads(r.read().decode())
                assert tk["symbol"] == "XAUUSD" and tk["tick"]["mid"] > 0

            req_cfg = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/settings",
                data=json.dumps({"lot_size": 0.02, "max_concurrent_trades": 3}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req_cfg, timeout=5).read()

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/auto-trade/start",
                data=json.dumps({"refresh_news": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                auto_res = json.loads(r.read().decode())
                assert auto_res["settings"]["auto_trade_enabled"] is True
                assert auto_res["pipeline"]["execution"]["executed"] is True, f"Expected execution: {auto_res['pipeline']['execution']}"
                trades = auto_res["pipeline"]["execution"]["trades"]
                assert len(trades) >= 1 and all(t["volume"] == 0.02 for t in trades)
                print("AUTO_TRADE_EXEC_OK", len(trades), auto_res["pipeline"]["execution"]["message"])
            req_stop = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/auto-trade/stop",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req_stop, timeout=5).read()
            print("HTTP_OK", port)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    run_tests()
