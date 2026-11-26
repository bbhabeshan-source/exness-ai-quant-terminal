"""
5-Stage Automated Trading Pipeline Orchestrator & Autopilot Loop:
1. Chart Technical Analysis across enabled pairs
2. Web & Financial News Scraping + Sentiment Scoring
3. Best Strategy Selection & Backtest Win-Rate Gate
4. Pair Ranking & Best Pair Selection (prioritizing Gold XAUUSD & GBPUSD)
5. Exness MT5 / Paper Execution + Position Risk Management
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from config import DEFAULT_PAIRS, TradingSettings
from database import Database
from engine.broker import ExnessBrokerManager
from engine.chart_analyzer import ChartAnalyzer
from engine.news_scraper import NewsScraper
from engine.pair_selector import PairSelector
from engine.strategy_selector import StrategySelector

logger = logging.getLogger("TradingPipeline")


class TradingPipeline:
    """Runs the 5-stage trading workflow on demand or on an autopilot schedule."""

    def __init__(self, db: Database, settings: TradingSettings, broker: ExnessBrokerManager):
        self.db = db
        self.settings = settings
        self.broker = broker
        self.news_scraper = NewsScraper(cache_ttl_seconds=180)
        self._lock = threading.Lock()
        self._latest_snapshot: Dict[str, Any] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start_background_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AutoPilotLoop")
        self._thread.start()

    def stop_background_loop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        last_cycle = 0.0
        last_quotes = 0.0
        while not self._stop_event.is_set():
            try:
                now = time.time()
                if (now - last_quotes) >= 5.0 and not self.broker.is_mt5_ready():
                    self.broker.live_feed.refresh_quotes()
                    last_quotes = now
                self.broker.sync_and_manage_positions()
                if self.settings.auto_trade_enabled and (now - last_cycle) >= max(10, self.settings.cycle_interval_seconds):
                    self.run_cycle(allow_execute=True)
                    last_cycle = now
            except Exception as exc:
                logger.exception(f"Autopilot loop error: {exc}")
            time.sleep(2.0)

    def run_cycle(self, allow_execute: bool = False, force_news_refresh: bool = False) -> Dict[str, Any]:
        with self._lock:
            if not self.broker.is_mt5_ready():
                self.broker.live_feed.refresh_quotes()
            self.broker.sync_and_manage_positions()
            ran_at = datetime.now(timezone.utc).isoformat()
            enabled = [p for p in self.settings.enabled_pairs if p in DEFAULT_PAIRS]
            if not enabled:
                enabled = ["XAUUSD", "GBPUSD"]

            # Stage 1: Analyze chart for every enabled pair in parallel (fast non-blocking)
            chart_results: Dict[str, Dict[str, Any]] = {}
            candles_cache: Dict[str, Any] = {}
            ticks_cache: Dict[str, Dict[str, Any]] = {}

            def _analyze_one(sym: str):
                spec = DEFAULT_PAIRS[sym]
                candles = self.broker.get_candles(sym, timeframe=self.settings.timeframe, count=350)
                tick = self.broker.get_tick(sym)
                chart = ChartAnalyzer.analyze(candles, sym, spec.pip_size)
                return sym, candles, tick, chart

            with ThreadPoolExecutor(max_workers=len(enabled)) as pool:
                for sym, candles, tick, chart in pool.map(_analyze_one, enabled):
                    candles_cache[sym] = candles
                    ticks_cache[sym] = tick
                    chart_results[sym] = chart

            # Stage 2: Scrape web & financial news AFTER chart analysis
            news_bundle = self.news_scraper.fetch_news(force_refresh=force_news_refresh)

            # Stage 3: Select best strategy per pair & evaluate backtest win rate
            pair_evaluations: List[Dict[str, Any]] = []
            for sym in enabled:
                spec = DEFAULT_PAIRS[sym]
                pair_news = self.news_scraper.get_pair_sentiment(sym, news_bundle)
                strat_eval = StrategySelector.select(
                    pair=spec,
                    candles=candles_cache[sym],
                    chart_analysis=chart_results[sym],
                    news_sentiment=pair_news,
                    settings=self.settings,
                )
                pair_evaluations.append(
                    {
                        "symbol": sym,
                        "tick": ticks_cache[sym],
                        "spread_pips": ticks_cache[sym]["spread_pips"],
                        "chart": chart_results[sym],
                        "news": pair_news,
                        "strategy": strat_eval,
                    }
                )

            # Stage 4: Rank pairs & pick best setup
            selection = PairSelector.rank_and_select(pair_evaluations, DEFAULT_PAIRS, self.settings)
            selected = selection.get("selected")

            # Stage 5: Execute trade directly using Stage 1-4 intelligence (no pre-trade win-rate block)
            execution_result: Dict[str, Any] = {
                "attempted": False,
                "executed": False,
                "message": selection.get("block_reason") or "Analysis complete (scan only).",
            }

            if allow_execute and selected:
                open_trades = self.db.list_trades(status="OPEN")
                open_symbols = {t["symbol"] for t in open_trades}
                slots_left = max(0, self.settings.max_concurrent_trades - len(open_trades))

                candidates = [
                    r for r in selection.get("rankings", [])
                    if r.get("executable") and r["symbol"] not in open_symbols
                ]

                if not selection["executable"] and not candidates:
                    execution_result = {
                        "attempted": False,
                        "executed": False,
                        "trades": [],
                        "message": selection["block_reason"],
                    }
                elif slots_left <= 0:
                    execution_result = {
                        "attempted": False,
                        "executed": False,
                        "trades": [],
                        "message": f"Max concurrent positions ({self.settings.max_concurrent_trades}) active and managed.",
                    }
                elif not candidates:
                    execution_result = {
                        "attempted": False,
                        "executed": False,
                        "trades": [],
                        "message": "All qualifying pairs already have active open positions.",
                    }
                else:
                    execution_result["attempted"] = True
                    executed_trades: List[Dict[str, Any]] = []
                    errors: List[str] = []
                    for target in candidates[:slots_left]:
                        res = self.broker.execute_trade(
                            canonical=target["symbol"],
                            direction=target["signal"],
                            volume=self.settings.lot_size,
                            sl_pips=target["sl_pips"],
                            tp_pips=target["tp_pips"],
                            strategy=target["best_strategy"],
                            confidence=target["confidence"],
                            backtest_win_rate=target["backtest_win_rate"],
                            news_bias=target["news_bias"],
                        )
                        if res.get("success") and res.get("trade"):
                            executed_trades.append(res["trade"])
                        elif res.get("error"):
                            errors.append(f"{target['symbol']}: {res['error']}")

                    execution_result["executed"] = len(executed_trades) > 0
                    execution_result["trades"] = executed_trades
                    execution_result["trade"] = executed_trades[0] if executed_trades else None
                    if executed_trades:
                        summary_str = ", ".join(
                            f"{t['direction']} {t['volume']} {t['symbol']}" for t in executed_trades
                        )
                        execution_result["message"] = (
                            f"Executed {len(executed_trades)} trade(s) via {self.settings.execution_mode}: {summary_str}"
                        )
                    else:
                        execution_result["message"] = "; ".join(errors) or "Execution failed."

            summary = {
                "ran_at": ran_at,
                "timeframe": self.settings.timeframe,
                "selected_pair": selection.get("selected_pair"),
                "selected_strategy": selected["best_strategy"] if selected else None,
                "signal_direction": selected["signal"] if selected else "HOLD",
                "confidence": selected["confidence"] if selected else 0.0,
                "backtest_win_rate": selected["backtest_win_rate"] if selected else 0.0,
                "executed": execution_result["executed"],
                "execution": execution_result,
                "rankings": selection.get("rankings", []),
                "news": news_bundle,
                "evaluations": {
                    ev["symbol"]: {
                        "tick": ev["tick"],
                        "chart": ev["chart"],
                        "news": ev["news"],
                        "strategy": ev["strategy"],
                    }
                    for ev in pair_evaluations
                },
            }

            self.db.record_pipeline_run(
                {
                    "ran_at": ran_at,
                    "selected_pair": summary["selected_pair"],
                    "selected_strategy": summary["selected_strategy"],
                    "signal_direction": summary["signal_direction"],
                    "confidence": summary["confidence"],
                    "backtest_win_rate": summary["backtest_win_rate"],
                    "executed": summary["executed"],
                    "message": execution_result["message"],
                }
            )
            self._latest_snapshot = summary
            return summary

    def get_latest_snapshot(self) -> Dict[str, Any]:
        if not self._latest_snapshot:
            return self.run_cycle(allow_execute=False)
        return self._latest_snapshot
