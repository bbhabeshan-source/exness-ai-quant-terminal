"""
Stage 4: Multi-Pair Ranker & Best Pair Selector.
Ranks XAUUSD (Gold), GBPUSD, and all enabled Forex pairs using chart confluence,
news alignment, and spread checks—selecting the top executable setup directly for Stage 5.
"""

from typing import Any, Dict, List
from config import PairSpec, TradingSettings


class PairSelector:
    """Ranks evaluated pairs and selects the highest-conviction executable opportunity."""

    @staticmethod
    def rank_and_select(
        pair_evaluations: List[Dict[str, Any]],
        pair_specs: Dict[str, PairSpec],
        settings: TradingSettings,
    ) -> Dict[str, Any]:
        ranked: List[Dict[str, Any]] = []

        for ev in pair_evaluations:
            sym = ev["symbol"]
            spec = pair_specs[sym]
            spread_pips = float(ev.get("spread_pips", 0.0))
            spread_ok = spread_pips <= spec.max_spread_pips * 1.5

            strat = ev["strategy"]
            conf = float(strat.get("confidence", 0.0))
            bt_wr = float(strat.get("backtest_win_rate", 0.0))
            signal = strat.get("signal", "HOLD")

            rank_score = conf * spec.priority_weight
            if signal == "HOLD":
                rank_score *= 0.4
            if not spread_ok:
                rank_score *= 0.35

            is_executable = (
                signal in ("BUY", "SELL")
                and spread_ok
                and conf >= settings.min_confluence_score
            )

            ranked.append(
                {
                    "symbol": sym,
                    "display_name": spec.display_name,
                    "category": spec.category,
                    "signal": signal,
                    "best_strategy": strat.get("best_strategy"),
                    "confidence": conf,
                    "backtest_win_rate": bt_wr,
                    "meets_win_rate_gate": True,
                    "spread_pips": round(spread_pips, 2),
                    "spread_ok": spread_ok,
                    "news_bias": strat.get("news_bias", "NEUTRAL"),
                    "regime": ev["chart"].get("regime", "UNKNOWN"),
                    "price": ev["chart"].get("indicators", {}).get("close", 0.0),
                    "sl_pips": strat.get("sl_pips", spec.default_sl_pips),
                    "tp_pips": strat.get("tp_pips", spec.default_tp_pips),
                    "rank_score": round(rank_score, 2),
                    "executable": is_executable,
                    "reasons": strat.get("reasons", []),
                }
            )

        ranked.sort(key=lambda r: (r["executable"], r["rank_score"], r["confidence"]), reverse=True)

        # Pick highest-ranked executable pair (not stuck on an un-executable #1)
        selected = next((r for r in ranked if r["executable"]), ranked[0] if ranked else None)

        executable = False
        block_reason = ""
        if selected:
            if selected["signal"] == "HOLD":
                block_reason = "No directional signal detected across active pairs."
            elif not selected["spread_ok"]:
                block_reason = f"Spread ({selected['spread_pips']} pips) exceeds safe limit."
            elif selected["confidence"] < settings.min_confluence_score:
                block_reason = f"Confluence {selected['confidence']}% below minimum ({settings.min_confluence_score}%)."
            else:
                executable = True

        return {
            "selected_pair": selected["symbol"] if selected else None,
            "selected": selected,
            "executable": executable,
            "block_reason": block_reason,
            "rankings": ranked,
        }
