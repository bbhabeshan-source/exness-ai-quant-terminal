"""
Configuration and pair specifications for Exness AI Automated Trading Platform.
Supports Gold (XAUUSD), GBPUSD, and major Forex pairs with Exness symbol suffix auto-mapping,
all standard timeframes (M1..H4), and full Exness MT5 server list.
"""

from dataclasses import dataclass, field, asdict
import os
from typing import Dict, List, Optional


SUPPORTED_TIMEFRAMES: List[str] = ["M1", "M5", "M15", "M30", "H1", "H4"]


def build_exness_server_list() -> List[str]:
    """Full list of Exness MT5 Demo (Trial) and Live (Real) servers."""
    servers: List[str] = []
    # Trial / Demo servers
    servers.append("Exness-MT5Trial")
    for i in range(2, 26):
        servers.append(f"Exness-MT5Trial{i}")
    # Real / Live servers
    servers.append("Exness-MT5Real")
    for i in range(2, 51):
        servers.append(f"Exness-MT5Real{i}")
    return servers


EXNESS_MT5_SERVERS: List[str] = build_exness_server_list()


@dataclass
class PairSpec:
    symbol: str                  # Canonical symbol e.g. XAUUSD, GBPUSD
    display_name: str
    category: str                # "COMMODITY" or "FOREX"
    pip_size: float              # 0.10 for XAUUSD, 0.0001 for GBPUSD/EURUSD, 0.01 for JPY pairs
    contract_size: float         # 100 for XAUUSD, 100000 for Forex
    max_spread_pips: float
    default_tp_pips: float
    default_sl_pips: float
    break_even_pips: float
    priority_weight: float = 1.0
    exness_aliases: List[str] = field(default_factory=list)
    yahoo_tickers: List[str] = field(default_factory=list)


DEFAULT_PAIRS: Dict[str, PairSpec] = {
    "XAUUSD": PairSpec(
        symbol="XAUUSD",
        display_name="Gold / US Dollar",
        category="COMMODITY",
        pip_size=0.10,
        contract_size=100.0,
        max_spread_pips=3.5,
        default_tp_pips=18.0,
        default_sl_pips=12.0,
        break_even_pips=8.0,
        priority_weight=1.15,
        exness_aliases=["XAUUSD", "XAUUSDm", "XAUUSDc", "XAUUSD.r", "GOLD"],
        yahoo_tickers=["XAUUSD=X", "GC=F"],
    ),
    "GBPUSD": PairSpec(
        symbol="GBPUSD",
        display_name="British Pound / US Dollar",
        category="FOREX",
        pip_size=0.0001,
        contract_size=100_000.0,
        max_spread_pips=2.0,
        default_tp_pips=14.0,
        default_sl_pips=10.0,
        break_even_pips=6.0,
        priority_weight=1.15,
        exness_aliases=["GBPUSD", "GBPUSDm", "GBPUSDc", "GBPUSD.r"],
        yahoo_tickers=["GBPUSD=X"],
    ),
    "EURUSD": PairSpec(
        symbol="EURUSD",
        display_name="Euro / US Dollar",
        category="FOREX",
        pip_size=0.0001,
        contract_size=100_000.0,
        max_spread_pips=1.8,
        default_tp_pips=12.0,
        default_sl_pips=9.0,
        break_even_pips=5.0,
        priority_weight=1.0,
        exness_aliases=["EURUSD", "EURUSDm", "EURUSDc", "EURUSD.r"],
        yahoo_tickers=["EURUSD=X"],
    ),
    "USDJPY": PairSpec(
        symbol="USDJPY",
        display_name="US Dollar / Japanese Yen",
        category="FOREX",
        pip_size=0.01,
        contract_size=100_000.0,
        max_spread_pips=2.2,
        default_tp_pips=14.0,
        default_sl_pips=10.0,
        break_even_pips=6.0,
        priority_weight=1.0,
        exness_aliases=["USDJPY", "USDJPYm", "USDJPYc", "USDJPY.r"],
        yahoo_tickers=["USDJPY=X"],
    ),
    "GBPJPY": PairSpec(
        symbol="GBPJPY",
        display_name="British Pound / Japanese Yen",
        category="FOREX",
        pip_size=0.01,
        contract_size=100_000.0,
        max_spread_pips=3.0,
        default_tp_pips=18.0,
        default_sl_pips=13.0,
        break_even_pips=8.0,
        priority_weight=0.95,
        exness_aliases=["GBPJPY", "GBPJPYm", "GBPJPYc", "GBPJPY.r"],
        yahoo_tickers=["GBPJPY=X"],
    ),
    "EURGBP": PairSpec(
        symbol="EURGBP",
        display_name="Euro / British Pound",
        category="FOREX",
        pip_size=0.0001,
        contract_size=100_000.0,
        max_spread_pips=1.8,
        default_tp_pips=10.0,
        default_sl_pips=8.0,
        break_even_pips=4.5,
        priority_weight=0.95,
        exness_aliases=["EURGBP", "EURGBPm", "EURGBPc", "EURGBP.r"],
        yahoo_tickers=["EURGBP=X"],
    ),
}


@dataclass
class TradingSettings:
    execution_mode: str = "PAPER"          # "PAPER" or "EXNESS_MT5"
    account_type: str = "DEMO"             # "DEMO" or "LIVE"
    live_confirmed: bool = False           # Explicit safety lock before live order routing
    auto_trade_enabled: bool = False       # Autopilot loop toggle
    cycle_interval_seconds: int = 12
    timeframe: str = "M15"                 # M1, M5, M15, M30, H1, H4
    enabled_pairs: List[str] = field(
        default_factory=lambda: ["XAUUSD", "GBPUSD", "EURUSD", "USDJPY", "GBPJPY", "EURGBP"]
    )
    lot_size: float = 0.01
    risk_per_trade_pct: float = 1.0
    max_concurrent_trades: int = 3
    max_daily_loss_pct: float = 5.0
    min_win_rate_gate: float = 85.0        # Retained only as optional display reference
    enforce_win_rate_gate: bool = False    # Never block Stage 5 execution on pre-trade win-rate math
    min_confluence_score: float = 48.0     # Minimum 0-100 combined technical + news score
    news_filter_enabled: bool = True
    mt5_login: Optional[int] = None
    mt5_password: Optional[str] = None
    mt5_server: str = "Exness-MT5Trial9"
    mt5_path: Optional[str] = field(
        default_factory=lambda: os.environ.get("EXNESS_MT5_PATH") or r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
    )

    def to_public_dict(self) -> dict:
        data = asdict(self)
        data["mt5_password_set"] = bool(self.mt5_password)
        data.pop("mt5_password", None)
        return data
