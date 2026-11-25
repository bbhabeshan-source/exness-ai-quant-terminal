"""
Stage 1: Chart & Multi-Indicator Technical Analysis Engine.
Computes EMAs, RSI, MACD, Bollinger Bands, ATR, ADX, swing levels, and market regime.
"""

from typing import Any, Dict, List
import numpy as np
import pandas as pd


class ChartAnalyzer:
    """Computes technical indicators and market structure from OHLCV candles."""

    @staticmethod
    def analyze(candles: pd.DataFrame, symbol: str, pip_size: float) -> Dict[str, Any]:
        if candles is None or candles.empty or len(candles) < 60:
            return {
                "symbol": symbol,
                "status": "INSUFFICIENT_DATA",
                "bias": "NEUTRAL",
                "regime": "UNKNOWN",
                "technical_score": 0.0,
                "indicators": {},
                "reasons": ["Need at least 60 candles for analysis."],
            }

        df = candles.copy().reset_index(drop=True)
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        # EMAs
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=min(200, len(df)), adjust=False).mean()

        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        rsi_series = 100.0 - (100.0 / (1.0 + rs))

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_signal = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_signal

        # Bollinger Bands (20, 2)
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std().fillna(0.0)
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std
        bb_width = ((bb_upper - bb_lower) / (bb_mid + 1e-10)) * 100.0

        # ATR (14)
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(14).mean()

        # ADX (14)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr_smooth = tr.ewm(alpha=1 / 14, adjust=False).mean() + 1e-10
        plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr_smooth
        minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr_smooth
        dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx_series = dx.ewm(alpha=1 / 14, adjust=False).mean()

        # Latest values
        c = float(close.iloc[-1])
        prev_c = float(close.iloc[-2])
        e9 = float(ema9.iloc[-1])
        e21 = float(ema21.iloc[-1])
        e50 = float(ema50.iloc[-1])
        e200 = float(ema200.iloc[-1])
        rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2])
        macd_h = float(macd_hist.iloc[-1])
        prev_macd_h = float(macd_hist.iloc[-2])
        atr = float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else pip_size * 10
        atr_pips = atr / pip_size
        adx = float(adx_series.iloc[-1])
        bb_u = float(bb_upper.iloc[-1])
        bb_l = float(bb_lower.iloc[-1])
        bb_m = float(bb_mid.iloc[-1])
        bb_w = float(bb_width.iloc[-1])
        bb_w_avg = float(bb_width.iloc[-20:].mean())

        swing_high = float(high.iloc[-24:].max())
        swing_low = float(low.iloc[-24:].min())

        # Determine regime
        if bb_w > bb_w_avg * 1.25 and adx >= 22:
            regime = "VOLATILE_BREAKOUT"
        elif adx >= 23 and (e9 > e21 > e50 or e9 < e21 < e50):
            regime = "TRENDING_UP" if e9 > e50 else "TRENDING_DOWN"
        else:
            regime = "RANGING"

        # Technical directional scoring (bullish vs bearish points)
        bull_pts = 0.0
        bear_pts = 0.0
        reasons: List[str] = []

        if e9 > e21 > e50:
            bull_pts += 28
            reasons.append("EMA 9 > 21 > 50 bullish stack")
        elif e9 < e21 < e50:
            bear_pts += 28
            reasons.append("EMA 9 < 21 < 50 bearish stack")
        elif e9 > e21:
            bull_pts += 14
            reasons.append("Fast EMA9 above EMA21")
        else:
            bear_pts += 14
            reasons.append("Fast EMA9 below EMA21")

        if c > e200:
            bull_pts += 15
            reasons.append("Price above EMA200 macro filter")
        else:
            bear_pts += 15
            reasons.append("Price below EMA200 macro filter")

        if 42 <= rsi <= 66 and rsi > prev_rsi:
            bull_pts += 18
            reasons.append(f"RSI({rsi:.1f}) rising in healthy bull zone")
        elif 34 <= rsi <= 58 and rsi < prev_rsi:
            bear_pts += 18
            reasons.append(f"RSI({rsi:.1f}) falling in healthy bear zone")
        elif rsi < 32:
            bull_pts += 16
            reasons.append(f"RSI({rsi:.1f}) oversold bounce zone")
        elif rsi > 68:
            bear_pts += 16
            reasons.append(f"RSI({rsi:.1f}) overbought exhaustion zone")

        if macd_h > 0 and macd_h >= prev_macd_h:
            bull_pts += 18
            reasons.append("MACD histogram expanding bullish")
        elif macd_h < 0 and macd_h <= prev_macd_h:
            bear_pts += 18
            reasons.append("MACD histogram expanding bearish")

        if adx >= 22:
            if bull_pts > bear_pts:
                bull_pts += 12
            else:
                bear_pts += 12
            reasons.append(f"Strong trend momentum ADX={adx:.1f}")

        # Price action candle momentum
        if c > prev_c and c > e21:
            bull_pts += 9
        elif c < prev_c and c < e21:
            bear_pts += 9

        if bull_pts > bear_pts + 10:
            bias = "BUY"
            tech_score = min(98.0, bull_pts)
        elif bear_pts > bull_pts + 10:
            bias = "SELL"
            tech_score = min(98.0, bear_pts)
        else:
            bias = "NEUTRAL"
            tech_score = max(bull_pts, bear_pts) * 0.75

        # Serialize up to 350 candles for historical chart panning & EMA overlays
        tail = df.tail(350).copy()
        tail_idx = tail.index
        chart_candles = []
        for i in tail_idx:
            row = df.loc[i]
            t_val = row["time"]
            t_str = t_val.isoformat() if hasattr(t_val, "isoformat") else str(t_val)
            chart_candles.append(
                {
                    "time": t_str,
                    "open": round(float(row["open"]), 5),
                    "high": round(float(row["high"]), 5),
                    "low": round(float(row["low"]), 5),
                    "close": round(float(row["close"]), 5),
                    "ema9": round(float(ema9.loc[i]), 5),
                    "ema21": round(float(ema21.loc[i]), 5),
                    "ema50": round(float(ema50.loc[i]), 5),
                    "bb_upper": round(float(bb_upper.loc[i]), 5) if not np.isnan(bb_upper.loc[i]) else None,
                    "bb_lower": round(float(bb_lower.loc[i]), 5) if not np.isnan(bb_lower.loc[i]) else None,
                }
            )

        return {
            "symbol": symbol,
            "status": "OK",
            "bias": bias,
            "regime": regime,
            "technical_score": round(tech_score, 1),
            "bull_points": round(bull_pts, 1),
            "bear_points": round(bear_pts, 1),
            "indicators": {
                "close": round(c, 5),
                "ema9": round(e9, 5),
                "ema21": round(e21, 5),
                "ema50": round(e50, 5),
                "ema200": round(e200, 5),
                "rsi": round(rsi, 2),
                "macd_hist": round(macd_h, 6),
                "atr": round(atr, 5),
                "atr_pips": round(atr_pips, 2),
                "adx": round(adx, 2),
                "bb_upper": round(bb_u, 5),
                "bb_mid": round(bb_m, 5),
                "bb_lower": round(bb_l, 5),
                "bb_width_pct": round(bb_w, 3),
                "swing_high": round(swing_high, 5),
                "swing_low": round(swing_low, 5),
            },
            "reasons": reasons,
            "series": chart_candles,
        }
