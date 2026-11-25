"""
Historical Walk-Forward Backtester for Strategy Win-Rate & Expectancy Validation.
Tests each candidate strategy on recent OHLCV candles before live/paper selection.
"""

from typing import Any, Dict, List
import numpy as np
import pandas as pd


STRATEGIES = (
    "EMA_PULLBACK_SNIPER",
    "LIQUIDITY_REVERSAL",
    "BREAKOUT_CATALYST",
)


class StrategyBacktester:
    """Evaluates candidate strategies on historical OHLCV bars."""

    @staticmethod
    def _simulate_strategy(
        df: pd.DataFrame,
        strategy_name: str,
        pip_size: float,
        sl_pips: float,
        tp_pips: float,
    ) -> Dict[str, Any]:
        if df is None or len(df) < 80:
            return {
                "strategy": strategy_name,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "net_pips": 0.0,
            }

        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values

        s_close = pd.Series(close)
        ema9 = s_close.ewm(span=9, adjust=False).mean().values
        ema21 = s_close.ewm(span=21, adjust=False).mean().values
        ema50 = s_close.ewm(span=50, adjust=False).mean().values

        delta = s_close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = (100.0 - (100.0 / (1.0 + gain / (loss + 1e-10)))).values

        bb_mid = s_close.rolling(20).mean().values
        bb_std = s_close.rolling(20).std().fillna(0.0).values
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std

        wins = 0
        losses = 0
        gross_win_pips = 0.0
        gross_loss_pips = 0.0
        n = len(df)
        i = 55

        while i < n - 12:
            direction = None

            if strategy_name == "EMA_PULLBACK_SNIPER":
                # High-probability pullback in aligned trend with early break-even protection
                if ema9[i] > ema21[i] > ema50[i] and low[i] <= ema21[i] * 1.0005 and close[i] > ema9[i] and 42 <= rsi[i] <= 65:
                    direction = "BUY"
                elif ema9[i] < ema21[i] < ema50[i] and high[i] >= ema21[i] * 0.9995 and close[i] < ema9[i] and 35 <= rsi[i] <= 58:
                    direction = "SELL"

            elif strategy_name == "LIQUIDITY_REVERSAL":
                # Mean-reversion after sweep beyond Bollinger band + RSI extreme
                if low[i] < bb_lower[i] and close[i] > bb_lower[i] and rsi[i] < 36:
                    direction = "BUY"
                elif high[i] > bb_upper[i] and close[i] < bb_upper[i] and rsi[i] > 64:
                    direction = "SELL"

            elif strategy_name == "BREAKOUT_CATALYST":
                # Momentum breakout above/below recent 10-bar range with EMA confirmation
                prev_high = float(np.max(high[i - 10 : i]))
                prev_low = float(np.min(low[i - 10 : i]))
                if close[i] > prev_high and ema9[i] > ema21[i] and rsi[i] > 55:
                    direction = "BUY"
                elif close[i] < prev_low and ema9[i] < ema21[i] and rsi[i] < 45:
                    direction = "SELL"

            if direction is None:
                i += 1
                continue

            entry = close[i]
            # High-win-rate TP/SL profile with break-even ratchet at 55% of TP
            tp_dist = tp_pips * pip_size
            sl_dist = sl_pips * pip_size
            be_trigger = tp_dist * 0.55

            tp_price = entry + tp_dist if direction == "BUY" else entry - tp_dist
            sl_price = entry - sl_dist if direction == "BUY" else entry + sl_dist
            be_armed = False
            outcome_pips = 0.0
            exit_bar = i + 1

            for j in range(i + 1, min(n, i + 28)):
                exit_bar = j
                if direction == "BUY":
                    if high[j] >= entry + be_trigger:
                        be_armed = True
                    if high[j] >= tp_price:
                        outcome_pips = tp_pips
                        break
                    eff_sl = max(sl_price, entry + 0.5 * pip_size) if be_armed else sl_price
                    if low[j] <= eff_sl:
                        outcome_pips = (eff_sl - entry) / pip_size
                        break
                else:
                    if low[j] <= entry - be_trigger:
                        be_armed = True
                    if low[j] <= tp_price:
                        outcome_pips = tp_pips
                        break
                    eff_sl = min(sl_price, entry - 0.5 * pip_size) if be_armed else sl_price
                    if high[j] >= eff_sl:
                        outcome_pips = (entry - eff_sl) / pip_size
                        break
            else:
                outcome_pips = (
                    (close[exit_bar] - entry) / pip_size
                    if direction == "BUY"
                    else (entry - close[exit_bar]) / pip_size
                )

            if outcome_pips >= 0:
                wins += 1
                gross_win_pips += outcome_pips
            else:
                losses += 1
                gross_loss_pips += abs(outcome_pips)

            i = exit_bar + 1

        total = wins + losses
        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        pf = (gross_win_pips / gross_loss_pips) if gross_loss_pips > 0 else (gross_win_pips if gross_win_pips > 0 else 0.0)

        return {
            "strategy": strategy_name,
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2),
            "net_pips": round(gross_win_pips - gross_loss_pips, 1),
        }

    @classmethod
    def evaluate_all(
        cls,
        candles: pd.DataFrame,
        pip_size: float,
        sl_pips: float,
        tp_pips: float,
    ) -> List[Dict[str, Any]]:
        results = [
            cls._simulate_strategy(candles, name, pip_size, sl_pips, tp_pips)
            for name in STRATEGIES
        ]
        results.sort(key=lambda r: (r["win_rate"], r["profit_factor"], r["trades"]), reverse=True)
        return results
