"""
Stage 3: Adaptive Best-Strategy Selector.
Combines chart regime, technical indicators, and news sentiment
to choose the best strategy and directional signal for Stage 4/5 execution,
without blocking Stage 5 on a pre-trade win-rate calculation.
"""

from typing import Any, Dict, List
import pandas as pd

from config import PairSpec, TradingSettings
from engine.backtester import StrategyBacktester


class StrategySelector:
    """Selects the optimal strategy given chart analysis and web news sentiment."""

    @staticmethod
    def select(
        pair: PairSpec,
        candles: pd.DataFrame,
        chart_analysis: Dict[str, Any],
        news_sentiment: Dict[str, Any],
        settings: TradingSettings,
    ) -> Dict[str, Any]:
        ind = chart_analysis.get("indicators", {})
        if not ind or chart_analysis.get("status") != "OK":
            return {
                "symbol": pair.symbol,
                "best_strategy": "NONE",
                "signal": "HOLD",
                "confidence": 0.0,
                "backtest_win_rate": 0.0,
                "sl_pips": pair.default_sl_pips,
                "tp_pips": pair.default_tp_pips,
                "reasons": ["Insufficient chart data."],
                "candidates": [],
            }

        atr_pips = max(4.0, float(ind.get("atr_pips", pair.default_sl_pips)))
        sl_pips = round(max(pair.default_sl_pips, atr_pips * 1.35), 1)
        tp_pips = round(max(pair.default_tp_pips, atr_pips * 1.55), 1)

        backtests = StrategyBacktester.evaluate_all(
            candles=candles,
            pip_size=pair.pip_size,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
        )

        regime = chart_analysis.get("regime", "RANGING")
        tech_bias = chart_analysis.get("bias", "NEUTRAL")
        bull_pts = float(chart_analysis.get("bull_points", 0.0))
        bear_pts = float(chart_analysis.get("bear_points", 0.0))
        tech_score = float(chart_analysis.get("technical_score", 0.0))
        news_bias = news_sentiment.get("bias", "NEUTRAL")
        news_score = float(news_sentiment.get("score", 50.0))
        high_impact = bool(news_sentiment.get("high_impact_warning", False))

        # Effective directional bias from chart + news confluence
        effective_bias = tech_bias
        if effective_bias == "NEUTRAL":
            if news_bias in ("BUY", "SELL"):
                effective_bias = news_bias
            elif abs(bull_pts - bear_pts) >= 2.0:
                effective_bias = "BUY" if bull_pts > bear_pts else "SELL"
            else:
                # Subtle EMA momentum fallback
                effective_bias = "BUY" if float(ind.get("ema9", 0)) >= float(ind.get("ema21", 0)) else "SELL"

        scored: List[Dict[str, Any]] = []
        for bt in backtests:
            name = bt["strategy"]
            if name == "EMA_PULLBACK_SNIPER" and regime in ("TRENDING_UP", "TRENDING_DOWN"):
                fit_bonus = 20.0
            elif name == "LIQUIDITY_REVERSAL" and regime == "RANGING":
                fit_bonus = 20.0
            elif name == "BREAKOUT_CATALYST" and regime == "VOLATILE_BREAKOUT":
                fit_bonus = 20.0
            else:
                fit_bonus = 7.0

            news_adj = 0.0
            if settings.news_filter_enabled and news_bias != "NEUTRAL":
                news_adj = 12.0 if news_bias == effective_bias else -6.0

            regime_conf = min(98.0, max(45.0, tech_score * 0.68 + fit_bonus + news_adj + min(bt["profit_factor"], 3.0) * 4.0))
            scored.append(
                {
                    **bt,
                    "regime_fit": fit_bonus,
                    "news_adjustment": news_adj,
                    "composite_score": round(regime_conf, 1),
                }
            )

        scored.sort(key=lambda s: (s["composite_score"], s["profit_factor"]), reverse=True)
        winner = scored[0]

        reasons = list(chart_analysis.get("reasons", []))
        if settings.news_filter_enabled and news_bias == effective_bias:
            reasons.append(f"News sentiment ({news_bias}) confirms {effective_bias} direction.")
        elif settings.news_filter_enabled and news_bias not in ("NEUTRAL", effective_bias):
            reasons.append(f"News bias ({news_bias}) differs from chart ({effective_bias}); adjusted confidence.")

        if high_impact:
            reasons.append("High-impact macro news active; trailing stop & break-even protection armed.")

        reasons.append(
            f"Strategy selected: {winner['strategy']} for {regime} regime (SL {sl_pips} / TP {tp_pips} pips)."
        )

        return {
            "symbol": pair.symbol,
            "best_strategy": winner["strategy"],
            "signal": effective_bias,
            "confidence": round(winner["composite_score"], 1),
            "backtest_win_rate": winner["win_rate"],
            "backtest_trades": winner["trades"],
            "backtest_profit_factor": winner["profit_factor"],
            "meets_win_rate_gate": True,  # Pre-trade win-rate blocking removed per user request
            "sl_pips": sl_pips,
            "tp_pips": tp_pips,
            "news_bias": news_bias,
            "news_score": news_score,
            "high_impact_warning": high_impact,
            "reasons": reasons,
            "candidates": scored,
        }
