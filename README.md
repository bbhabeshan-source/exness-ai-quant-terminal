# Exness AI Quant Terminal

Automated Forex & Gold (XAUUSD) trading web application with a 5-stage pipeline:
1. **Chart Analysis**: EMA 9/21/50/200, RSI(14), MACD, Bollinger Bands, ATR, ADX, and Market Regime detection.
2. **Web News Scraping**: Live RSS/web feeds (FXStreet, Investing.com, Google News) with per-pair sentiment and high-impact macro event detection.
3. **Adaptive Strategy Selection & Backtest Gate**: Compares `EMA_PULLBACK_SNIPER`, `LIQUIDITY_REVERSAL`, and `BREAKOUT_CATALYST` on recent candles with a configurable historical win-rate gate (default `85%`).
4. **Pair Ranker**: Evaluates **XAUUSD**, **GBPUSD**, **EURUSD**, **USDJPY**, **GBPJPY**, and **EURGBP** and selects the highest-conviction setup.
5. **Exness MT5 & Paper Execution**: Routes trades to your local `MetaTrader 5 EXNESS` terminal (Demo or Live with explicit safety confirmation) or the built-in SQLite Paper Sandbox.

## Quick Start
```powershell
cd C:\Users\HP\.gemini\antigravity\scratch\exness_ai_trader
python server.py
```
Then open the URL printed in the terminal (default `http://127.0.0.1:8899`) in your browser.
