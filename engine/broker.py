"""
Stage 5: Dual Broker Execution Engine for Exness MetaTrader 5 & Live-Anchored Paper Sandbox.
Supports Exness symbol suffixes (XAUUSDm, GBPUSDm, XAUUSD, GBPUSD),
local MT5 profile/server discovery, direct login via mt5.initialize(),
all timeframes M1..H4, and live public market quotes via LiveMarketFeed.
"""

from datetime import datetime, timezone
import glob
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
import pandas as pd

from config import DEFAULT_PAIRS, EXNESS_MT5_SERVERS, TradingSettings
from database import Database
from engine.live_feed import LiveMarketFeed

logger = logging.getLogger("BrokerEngine")

try:
    import MetaTrader5 as mt5
    MT5_IMPORTED = True
except ImportError:
    mt5 = None
    MT5_IMPORTED = False


EXNESS_DEFAULT_PATHS = [
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
]


def _read_text_auto(path: str) -> str:
    raw = open(path, "rb").read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:64]:
        return raw.decode("utf-16", errors="ignore").replace("\x00", "")
    return raw.decode("utf-8", errors="ignore").replace("\x00", "")


def discover_local_mt5_profiles() -> List[Dict[str, str]]:
    """Reads %APPDATA%\\MetaQuotes\\Terminal\\*\\origin.txt and common.ini for local MT5 logins/servers."""
    profiles: List[Dict[str, str]] = []
    base = os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal")
    if not os.path.isdir(base):
        return profiles

    for folder in glob.glob(os.path.join(base, "*")):
        if not os.path.isdir(folder):
            continue
        origin_file = os.path.join(folder, "origin.txt")
        ini_file = os.path.join(folder, "config", "common.ini")
        origin = ""
        login = ""
        server = ""
        if os.path.isfile(origin_file):
            try:
                origin = _read_text_auto(origin_file).strip()
            except Exception:
                pass
        if os.path.isfile(ini_file):
            try:
                raw = _read_text_auto(ini_file)
                m_login = re.search(r"^Login=(\d+)", raw, re.MULTILINE)
                m_srv = re.search(r"^Server=([^\r\n]+)", raw, re.MULTILINE)
                if m_login:
                    login = m_login.group(1).strip()
                if m_srv:
                    server = m_srv.group(1).strip()
            except Exception:
                pass
        if origin or server:
            exe_path = os.path.join(origin, "terminal64.exe") if origin else ""
            profiles.append(
                {
                    "terminal_dir": origin,
                    "exe_path": exe_path,
                    "login": login,
                    "server": server,
                }
            )
    return profiles


class ExnessBrokerManager:
    """Unified broker interface routing to either Exness MetaTrader 5 or Live-Feed Paper Sandbox."""

    def __init__(self, db: Database, settings: TradingSettings):
        self.db = db
        self.settings = settings
        self.live_feed = LiveMarketFeed()
        self._mt5_connected = False
        self._symbol_map: Dict[str, str] = {}
        self._last_mt5_error: str = ""
        self._lock = threading.Lock()
        if self.db.get_state("paper_balance") is None:
            self.db.set_state("paper_balance", "1000.0")

    def get_available_servers(self) -> Dict[str, Any]:
        profiles = discover_local_mt5_profiles()
        discovered = [p["server"] for p in profiles if p.get("server") and "exness" in p["server"].lower()]
        all_servers: List[str] = []
        for s in discovered + EXNESS_MT5_SERVERS:
            if s and s not in all_servers:
                all_servers.append(s)
        exness_profiles = [p for p in profiles if "exness" in (p.get("server", "") + p.get("terminal_dir", "")).lower()]
        return {
            "servers": all_servers,
            "detected_profiles": exness_profiles,
            "detected_paths": [p for p in EXNESS_DEFAULT_PATHS if os.path.exists(p)],
        }

    def _candidate_paths(self) -> List[Optional[str]]:
        paths: List[Optional[str]] = []
        if self.settings.mt5_path and os.path.exists(self.settings.mt5_path):
            paths.append(self.settings.mt5_path)
        for p in EXNESS_DEFAULT_PATHS:
            if os.path.exists(p) and p not in paths:
                paths.append(p)
        paths.append(None)  # Auto-attach fallback
        return paths

    def connect_mt5(self) -> Dict[str, Any]:
        if not MT5_IMPORTED:
            self._last_mt5_error = "MetaTrader5 Python package is not installed."
            return {"connected": False, "error": self._last_mt5_error}

        with self._lock:
            has_creds = bool(self.settings.mt5_login and self.settings.mt5_password and self.settings.mt5_server)
            last_err: Any = None
            used_path: Optional[str] = None
            initialized = False

            for candidate_path in self._candidate_paths():
                mt5.shutdown()
                init_kwargs: Dict[str, Any] = {"timeout": 15000}
                if candidate_path:
                    init_kwargs["path"] = candidate_path
                # Passing login/password/server directly to initialize() avoids (-6, Authorization failed)
                if has_creds:
                    init_kwargs["login"] = int(self.settings.mt5_login)
                    init_kwargs["password"] = str(self.settings.mt5_password)
                    init_kwargs["server"] = str(self.settings.mt5_server)

                if mt5.initialize(**init_kwargs):
                    initialized = True
                    used_path = candidate_path
                    break
                last_err = mt5.last_error()

            if not initialized:
                self._mt5_connected = False
                if isinstance(last_err, tuple) and last_err[0] == -6:
                    if has_creds:
                        self._last_mt5_error = (
                            f"Exness MT5 authorization failed for account #{self.settings.mt5_login} on "
                            f"{self.settings.mt5_server}. Verify your Login ID, Password, and exact Exness Server name."
                        )
                    else:
                        self._last_mt5_error = (
                            "MT5 terminal is not logged into an active Exness account yet. "
                            "Enter your Exness Login ID, Password, and select your Exness server (Demo or Live) to connect."
                        )
                else:
                    self._last_mt5_error = f"MT5 initialize failed: {last_err}"
                return {"connected": False, "error": self._last_mt5_error, "terminal_path": used_path}

            acc = mt5.account_info()
            term = mt5.terminal_info()
            if acc is None:
                mt5.shutdown()
                self._mt5_connected = False
                self._last_mt5_error = "MT5 connected, but no trading account is signed in."
                return {"connected": False, "error": self._last_mt5_error, "terminal_path": used_path}

            # Auto-discover Exness symbol names (e.g. XAUUSDm, GBPUSDm, XAUUSD)
            self._symbol_map = {}
            all_syms = mt5.symbols_get() or []
            available = {s.name.upper(): s.name for s in all_syms}
            for canonical, spec in DEFAULT_PAIRS.items():
                matched = None
                for alias in spec.exness_aliases:
                    if alias.upper() in available:
                        matched = available[alias.upper()]
                        break
                if not matched:
                    for s in all_syms:
                        if canonical in s.name.upper():
                            matched = s.name
                            break
                if matched:
                    mt5.symbol_select(matched, True)
                    self._symbol_map[canonical] = matched

            self._mt5_connected = True
            self._last_mt5_error = ""
            return {
                "connected": True,
                "terminal_path": used_path,
                "login": acc.login,
                "server": acc.server,
                "trade_mode": "DEMO" if acc.trade_mode == 0 else "LIVE",
                "algo_trading_allowed": bool(term.trade_allowed) if term else False,
                "balance": round(acc.balance, 2),
                "equity": round(acc.equity, 2),
                "currency": acc.currency,
                "symbol_map": self._symbol_map,
            }

    def disconnect_mt5(self) -> None:
        with self._lock:
            if MT5_IMPORTED and self._mt5_connected:
                mt5.shutdown()
            self._mt5_connected = False

    def is_mt5_ready(self) -> bool:
        if not MT5_IMPORTED or not self._mt5_connected:
            return False
        try:
            return mt5.terminal_info() is not None and mt5.account_info() is not None
        except Exception:
            self._mt5_connected = False
            return False

    def get_broker_symbol(self, canonical: str) -> str:
        return self._symbol_map.get(canonical, canonical)

    def get_account_summary(self) -> Dict[str, Any]:
        stats = self.db.get_trade_stats()
        if self.settings.execution_mode == "EXNESS_MT5" and self.is_mt5_ready():
            acc = mt5.account_info()
            term = mt5.terminal_info()
            return {
                "mode": "EXNESS_MT5",
                "mt5_connected": True,
                "connected": True,
                "login": acc.login,
                "server": acc.server,
                "account_type": "DEMO" if acc.trade_mode == 0 else "LIVE",
                "algo_allowed": bool(term.trade_allowed) if term else False,
                "balance": round(acc.balance, 2),
                "equity": round(acc.equity, 2),
                "margin": round(acc.margin, 2),
                "free_margin": round(acc.margin_free, 2),
                "profit": round(acc.profit, 2),
                "currency": acc.currency,
                "symbol_map": self._symbol_map,
                "stats": stats,
                "last_error": self._last_mt5_error,
                "exness_terminal_detected": True,
            }

        paper_bal = float(self.db.get_state("paper_balance", "1000.0") or "1000.0")
        open_trades = self.db.list_trades(status="OPEN")
        unrealized = sum(float(t.get("profit", 0.0)) for t in open_trades)
        return {
            "mode": self.settings.execution_mode,
            "mt5_connected": self.is_mt5_ready(),
            "connected": self.settings.execution_mode == "PAPER",
            "login": "PAPER-EXNESS",
            "server": "Exness-PaperSandbox (Live Market Feed)",
            "account_type": "PAPER",
            "algo_allowed": True,
            "balance": round(paper_bal, 2),
            "equity": round(paper_bal + unrealized, 2),
            "margin": round(len(open_trades) * 15.0, 2),
            "free_margin": round(paper_bal + unrealized - len(open_trades) * 15.0, 2),
            "profit": round(unrealized, 2),
            "currency": "USD",
            "symbol_map": self._symbol_map,
            "stats": stats,
            "last_error": self._last_mt5_error,
            "exness_terminal_detected": any(os.path.exists(p) for p in EXNESS_DEFAULT_PATHS),
        }

    def get_candles(self, canonical: str, timeframe: str = "M15", count: int = 350) -> pd.DataFrame:
        if self.is_mt5_ready():
            tf_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1,
                "H4": mt5.TIMEFRAME_H4,
            }
            broker_sym = self.get_broker_symbol(canonical)
            rates = mt5.copy_rates_from_pos(broker_sym, tf_map.get(timeframe, mt5.TIMEFRAME_M15), 0, count)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                return df[["time", "open", "high", "low", "close", "tick_volume"]]

        return self.live_feed.get_candles(canonical, timeframe=timeframe, count=count)

    def get_tick(self, canonical: str) -> Dict[str, Any]:
        spec = DEFAULT_PAIRS[canonical]
        if self.is_mt5_ready():
            broker_sym = self.get_broker_symbol(canonical)
            tick = mt5.symbol_info_tick(broker_sym)
            if tick is not None and tick.bid > 0 and tick.ask > 0:
                spread_pips = round((tick.ask - tick.bid) / spec.pip_size, 2)
                return {
                    "symbol": canonical,
                    "broker_symbol": broker_sym,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "mid": round((tick.bid + tick.ask) / 2.0, 5),
                    "spread_pips": spread_pips,
                    "time": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
                    "source": "EXNESS_MT5",
                }
        return self.live_feed.get_tick(canonical)

    def execute_trade(
        self,
        canonical: str,
        direction: str,
        volume: float,
        sl_pips: float,
        tp_pips: float,
        strategy: str,
        confidence: float,
        backtest_win_rate: float,
        news_bias: str,
    ) -> Dict[str, Any]:
        spec = DEFAULT_PAIRS[canonical]
        tick = self.get_tick(canonical)
        entry_price = tick["ask"] if direction == "BUY" else tick["bid"]
        sl_dist = sl_pips * spec.pip_size
        tp_dist = tp_pips * spec.pip_size
        sl = round(entry_price - sl_dist if direction == "BUY" else entry_price + sl_dist, 5)
        tp = round(entry_price + tp_dist if direction == "BUY" else entry_price - tp_dist, 5)

        if self.settings.execution_mode == "EXNESS_MT5":
            if not self.is_mt5_ready():
                return {"success": False, "error": "Exness MT5 is not connected."}
            acc = mt5.account_info()
            if acc and acc.trade_mode != 0 and not self.settings.live_confirmed:
                return {
                    "success": False,
                    "error": "Live Exness account detected. Enable 'Confirm Live Trading' before executing real orders.",
                }

            broker_sym = self.get_broker_symbol(canonical)
            sym_info = mt5.symbol_info(broker_sym)
            if sym_info is None:
                return {"success": False, "error": f"Symbol {broker_sym} unavailable in Exness MT5."}

            digits = sym_info.digits
            price = tick["ask"] if direction == "BUY" else tick["bid"]
            sl_r = round(price - sl_dist if direction == "BUY" else price + sl_dist, digits)
            tp_r = round(price + tp_dist if direction == "BUY" else price - tp_dist, digits)
            order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

            res = None
            for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": broker_sym,
                    "volume": float(volume),
                    "type": order_type,
                    "price": price,
                    "sl": sl_r,
                    "tp": tp_r,
                    "deviation": 25,
                    "magic": 85001,
                    "comment": f"AI:{strategy[:12]}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                res = mt5.order_send(request)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    ticket = int(res.order or res.deal or int(time.time()))
                    trade_rec = {
                        "ticket": ticket,
                        "mode": "EXNESS_MT5",
                        "symbol": canonical,
                        "broker_symbol": broker_sym,
                        "direction": direction,
                        "volume": volume,
                        "open_price": res.price or price,
                        "sl": sl_r,
                        "tp": tp_r,
                        "profit": 0.0,
                        "strategy": strategy,
                        "confidence": confidence,
                        "backtest_win_rate": backtest_win_rate,
                        "news_bias": news_bias,
                        "comment": f"Exness MT5 ({filling})",
                    }
                    self.db.record_trade_open(trade_rec)
                    return {"success": True, "trade": trade_rec}

            err_msg = f"MT5 order_send failed: {res.retcode if res else mt5.last_error()} {res.comment if res else ''}"
            return {"success": False, "error": err_msg}

        ticket = int(time.time() * 1000) % 10_000_000
        trade_rec = {
            "ticket": ticket,
            "mode": "PAPER",
            "symbol": canonical,
            "broker_symbol": canonical,
            "direction": direction,
            "volume": volume,
            "open_price": entry_price,
            "sl": sl,
            "tp": tp,
            "profit": 0.0,
            "strategy": strategy,
            "confidence": confidence,
            "backtest_win_rate": backtest_win_rate,
            "news_bias": news_bias,
            "comment": "Paper Sandbox (Live Quotes)",
        }
        self.db.record_trade_open(trade_rec)
        return {"success": True, "trade": trade_rec}

    def sync_and_manage_positions(self) -> None:
        open_trades = self.db.list_trades(status="OPEN")
        if not open_trades:
            return

        if self.settings.execution_mode == "EXNESS_MT5" and self.is_mt5_ready():
            mt5_positions = {p.ticket: p for p in (mt5.positions_get() or [])}
            for tr in open_trades:
                if tr["mode"] != "EXNESS_MT5":
                    continue
                tkt = tr["ticket"]
                if tkt in mt5_positions:
                    pos = mt5_positions[tkt]
                    new_sl = pos.sl
                    spec = DEFAULT_PAIRS.get(tr["symbol"])
                    if spec:
                        is_buy = pos.type == mt5.ORDER_TYPE_BUY
                        pips_diff = (
                            (pos.price_current - pos.price_open) / spec.pip_size
                            if is_buy
                            else (pos.price_open - pos.price_current) / spec.pip_size
                        )
                        if pips_diff >= spec.break_even_pips:
                            trail_dist = max(spec.break_even_pips * 0.75, 4.0) * spec.pip_size
                            candidate_sl = (
                                max(pos.price_open + spec.pip_size * 0.5, pos.price_current - trail_dist)
                                if is_buy
                                else min(pos.price_open - spec.pip_size * 0.5, pos.price_current + trail_dist)
                            )
                            sym_info = mt5.symbol_info(pos.symbol)
                            digits = sym_info.digits if sym_info else 5
                            candidate_sl = round(candidate_sl, digits)
                            if (is_buy and candidate_sl > pos.sl + spec.pip_size * 0.3) or (
                                not is_buy and (pos.sl == 0 or candidate_sl < pos.sl - spec.pip_size * 0.3)
                            ):
                                mod = mt5.order_send(
                                    {
                                        "action": mt5.TRADE_ACTION_SLTP,
                                        "symbol": pos.symbol,
                                        "position": pos.ticket,
                                        "sl": candidate_sl,
                                        "tp": pos.tp,
                                    }
                                )
                                if mod and mod.retcode == mt5.TRADE_RETCODE_DONE:
                                    new_sl = candidate_sl
                    self.db.update_open_trade_sl_profit(tkt, new_sl, pos.profit)
                else:
                    tick = self.get_tick(tr["symbol"])
                    self.db.close_trade(tkt, tick["mid"], float(tr.get("profit", 0.0)), status="CLOSED_MT5")
            return

        paper_bal = float(self.db.get_state("paper_balance", "1000.0") or "1000.0")
        for tr in open_trades:
            if tr["mode"] != "PAPER":
                continue
            spec = DEFAULT_PAIRS.get(tr["symbol"])
            if not spec:
                continue
            tick = self.get_tick(tr["symbol"])
            cur_price = tick["bid"] if tr["direction"] == "BUY" else tick["ask"]
            pip_val_per_lot = spec.pip_size * spec.contract_size
            if tr["symbol"].endswith("JPY"):
                pip_val_per_lot = (spec.pip_size * spec.contract_size) / max(cur_price, 1.0)
            pips_diff = (
                (cur_price - tr["open_price"]) / spec.pip_size
                if tr["direction"] == "BUY"
                else (tr["open_price"] - cur_price) / spec.pip_size
            )
            profit = pips_diff * pip_val_per_lot * tr["volume"]

            # Break-Even + Trailing Stop profit protection
            sl = float(tr["sl"])
            if pips_diff >= spec.break_even_pips:
                trail_dist = max(spec.break_even_pips * 0.75, 4.0) * spec.pip_size
                candidate_sl = (
                    max(tr["open_price"] + spec.pip_size * 0.5, cur_price - trail_dist)
                    if tr["direction"] == "BUY"
                    else min(tr["open_price"] - spec.pip_size * 0.5, cur_price + trail_dist)
                )
                if (tr["direction"] == "BUY" and candidate_sl > sl) or (
                    tr["direction"] == "SELL" and candidate_sl < sl
                ):
                    sl = round(candidate_sl, 5)

            hit_tp = (tr["direction"] == "BUY" and cur_price >= tr["tp"]) or (
                tr["direction"] == "SELL" and cur_price <= tr["tp"]
            )
            hit_sl = (tr["direction"] == "BUY" and cur_price <= sl) or (
                tr["direction"] == "SELL" and cur_price >= sl
            )

            if hit_tp or hit_sl:
                status = "CLOSED_TP" if hit_tp else "CLOSED_SL"
                self.db.close_trade(tr["ticket"], cur_price, profit, status=status)
                paper_bal += profit
                self.db.set_state("paper_balance", f"{paper_bal:.2f}")
            else:
                self.db.update_open_trade_sl_profit(tr["ticket"], sl, profit)

    def close_position_by_ticket(self, ticket: int) -> Dict[str, Any]:
        open_trades = self.db.list_trades(status="OPEN")
        target = next((t for t in open_trades if int(t["ticket"]) == int(ticket)), None)
        if not target:
            return {"success": False, "error": f"Open trade #{ticket} not found."}

        if target["mode"] == "EXNESS_MT5" and self.is_mt5_ready():
            pos_list = mt5.positions_get(ticket=int(ticket))
            if pos_list:
                pos = pos_list[0]
                close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(pos.symbol)
                price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
                res = mt5.order_send(
                    {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": pos.symbol,
                        "volume": pos.volume,
                        "type": close_type,
                        "position": pos.ticket,
                        "price": price,
                        "deviation": 25,
                        "magic": 85001,
                        "comment": "AI Web Close",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                )
                if not res or res.retcode != mt5.TRADE_RETCODE_DONE:
                    return {"success": False, "error": f"MT5 close failed: {res.retcode if res else mt5.last_error()}"}
                self.db.close_trade(int(ticket), price, pos.profit, status="CLOSED_MANUAL")
                return {"success": True, "ticket": ticket, "profit": pos.profit}

        tick = self.get_tick(target["symbol"])
        close_px = tick["bid"] if target["direction"] == "BUY" else tick["ask"]
        profit = float(target.get("profit", 0.0))
        self.db.close_trade(int(ticket), close_px, profit, status="CLOSED_MANUAL")
        paper_bal = float(self.db.get_state("paper_balance", "1000.0") or "1000.0") + profit
        self.db.set_state("paper_balance", f"{paper_bal:.2f}")
        return {"success": True, "ticket": ticket, "profit": round(profit, 2)}
