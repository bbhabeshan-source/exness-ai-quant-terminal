"""
SQLite persistence for trades, pipeline runs, news snapshots, and settings.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    broker_symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    volume REAL NOT NULL,
                    open_price REAL NOT NULL,
                    close_price REAL,
                    sl REAL NOT NULL,
                    tp REAL NOT NULL,
                    profit REAL DEFAULT 0.0,
                    strategy TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    backtest_win_rate REAL DEFAULT 0.0,
                    news_bias TEXT DEFAULT 'NEUTRAL',
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    comment TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ran_at TEXT NOT NULL,
                    selected_pair TEXT,
                    selected_strategy TEXT,
                    signal_direction TEXT,
                    confidence REAL,
                    backtest_win_rate REAL,
                    executed INTEGER DEFAULT 0,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM account_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO account_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def record_trade_open(self, trade: Dict[str, Any]) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    ticket, mode, symbol, broker_symbol, direction, volume,
                    open_price, sl, tp, profit, strategy, confidence,
                    backtest_win_rate, news_bias, status, opened_at, comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    trade["ticket"],
                    trade["mode"],
                    trade["symbol"],
                    trade["broker_symbol"],
                    trade["direction"],
                    trade["volume"],
                    trade["open_price"],
                    trade["sl"],
                    trade["tp"],
                    trade.get("profit", 0.0),
                    trade["strategy"],
                    trade["confidence"],
                    trade.get("backtest_win_rate", 0.0),
                    trade.get("news_bias", "NEUTRAL"),
                    trade.get("opened_at") or datetime.now(timezone.utc).isoformat(),
                    trade.get("comment", ""),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def close_trade(self, ticket: int, close_price: float, profit: float, status: str = "CLOSED") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET close_price = ?, profit = ?, status = ?, closed_at = ?
                WHERE ticket = ? AND status = 'OPEN'
                """,
                (close_price, round(profit, 2), status, datetime.now(timezone.utc).isoformat(), ticket),
            )
            conn.commit()

    def update_open_trade_sl_profit(self, ticket: int, sl: float, profit: float) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE trades SET sl = ?, profit = ? WHERE ticket = ? AND status = 'OPEN'",
                (sl, round(profit, 2), ticket),
            )
            conn.commit()

    def list_trades(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_trade_stats(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            closed = conn.execute(
                "SELECT profit FROM trades WHERE status != 'OPEN'"
            ).fetchall()
            open_rows = conn.execute(
                "SELECT profit FROM trades WHERE status = 'OPEN'"
            ).fetchall()

            profits = [float(r["profit"]) for r in closed]
            wins = [p for p in profits if p > 0]
            losses = [p for p in profits if p < 0]
            total_closed = len(profits)
            win_rate = (len(wins) / total_closed * 100.0) if total_closed > 0 else 0.0
            realized_pnl = sum(profits)
            unrealized_pnl = sum(float(r["profit"]) for r in open_rows)
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)

            return {
                "total_closed": total_closed,
                "open_count": len(open_rows),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "profit_factor": round(profit_factor, 2),
            }

    def record_pipeline_run(self, summary: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (
                    ran_at, selected_pair, selected_strategy, signal_direction,
                    confidence, backtest_win_rate, executed, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.get("ran_at") or datetime.now(timezone.utc).isoformat(),
                    summary.get("selected_pair"),
                    summary.get("selected_strategy"),
                    summary.get("signal_direction"),
                    summary.get("confidence", 0.0),
                    summary.get("backtest_win_rate", 0.0),
                    1 if summary.get("executed") else 0,
                    json.dumps(summary),
                ),
            )
            conn.commit()

    def list_pipeline_runs(self, limit: int = 15) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ran_at, selected_pair, selected_strategy, signal_direction, "
                "confidence, backtest_win_rate, executed FROM pipeline_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
