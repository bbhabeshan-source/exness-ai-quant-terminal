"""
Stage 2: Web & Financial News Scraper + Currency Sentiment Engine.
Runs AFTER chart analysis to scrape live RSS/web headlines for Gold (XAUUSD) and Forex pairs,
scoring sentiment per base/quote currency and flagging high-impact macro catalysts.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any, Dict, List
import xml.etree.ElementTree as ET
import requests

logger = logging.getLogger("NewsScraper")

NEWS_FEEDS = [
    {
        "source": "FXStreet News",
        "url": "https://www.fxstreet.com/rss/news",
    },
    {
        "source": "Investing.com Forex",
        "url": "https://www.investing.com/rss/news_1.rss",
    },
    {
        "source": "Investing.com Commodities",
        "url": "https://www.investing.com/rss/news_11.rss",
    },
    {
        "source": "Google News Gold & Forex",
        "url": "https://news.google.com/rss/search?q=XAUUSD+OR+GBPUSD+OR+Gold+price+forex&hl=en-US&gl=US&ceid=US:en",
    },
]

BULLISH_TERMS = {
    "rally": 1.4, "surge": 1.6, "soar": 1.6, "gains": 1.2, "bullish": 1.5,
    "breakout": 1.3, "higher": 1.0, "climb": 1.1, "rebound": 1.2, "jump": 1.3,
    "strong": 1.1, "hawkish": 1.4, "beat": 1.2, "safe haven": 1.3, "support": 0.8,
    "advance": 1.1, "rise": 1.0, "record high": 1.6, "upside": 1.2,
}

BEARISH_TERMS = {
    "drop": 1.3, "fall": 1.2, "slump": 1.5, "plunge": 1.6, "bearish": 1.5,
    "decline": 1.2, "retreat": 1.1, "sell-off": 1.5, "selloff": 1.5, "weak": 1.2,
    "dovish": 1.4, "miss": 1.2, "pressure": 1.0, "lower": 1.0, "tumble": 1.5,
    "downside": 1.2, "slip": 1.0, "dip": 0.9, "resistance": 0.7,
}

HIGH_IMPACT_TERMS = [
    "fomc", "fed", "federal reserve", "cpi", "inflation", "nfp", "nonfarm",
    "payroll", "boe", "bank of england", "ecb", "boj", "gdp", "interest rate",
    "rate cut", "rate hike", "powell", "bailey", "geopolitical",
]

PAIR_KEYWORDS = {
    "XAUUSD": ["gold", "xau", "xauusd", "bullion", "precious metal", "safe haven", "fed", "treasury yield"],
    "GBPUSD": ["gbp", "pound", "sterling", "gbpusd", "uk", "boe", "bank of england", "britain", "cable"],
    "EURUSD": ["eur", "euro", "eurusd", "ecb", "eurozone", "lagarde"],
    "USDJPY": ["jpy", "yen", "usdjpy", "boj", "bank of japan", "ueda"],
    "GBPJPY": ["gbp", "pound", "jpy", "yen", "gbpjpy", "boe", "boj"],
    "EURGBP": ["eur", "gbp", "eurgbp", "ecb", "boe"],
    "BTCUSD": ["bitcoin", "btc", "crypto"],
}


class NewsScraper:
    """Scrapes live web/RSS feeds and computes per-pair sentiment with short caching."""

    def __init__(self, cache_ttl_seconds: int = 180):
        self.cache_ttl_seconds = cache_ttl_seconds
        self._last_fetch: float = 0.0
        self._cached_items: List[Dict[str, Any]] = []
        self._cached_sources: List[Dict[str, Any]] = []

    @staticmethod
    def _clean_html(text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text or "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _score_headline(title: str, summary: str) -> Dict[str, Any]:
        combined = f"{title} {summary}".lower()
        bull_score = sum(w for term, w in BULLISH_TERMS.items() if term in combined)
        bear_score = sum(w for term, w in BEARISH_TERMS.items() if term in combined)

        # Dollar strength inversely impacts XAUUSD / GBPUSD / EURUSD when headline is USD-centric
        usd_centric = any(k in combined for k in ["dollar", "usd", "dxy", "greenback", "fed", "powell"])
        high_impact = any(k in combined for k in HIGH_IMPACT_TERMS)

        net = bull_score - bear_score
        if net > 0.6:
            sentiment = "BULLISH"
        elif net < -0.6:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"

        matched_pairs = [
            pair for pair, keys in PAIR_KEYWORDS.items() if any(k in combined for k in keys)
        ]
        if not matched_pairs and usd_centric:
            matched_pairs = ["XAUUSD", "GBPUSD", "EURUSD", "USDJPY"]

        return {
            "sentiment": sentiment,
            "raw_score": round(net, 2),
            "usd_centric": usd_centric,
            "high_impact": high_impact,
            "pairs": matched_pairs or ["FOREX"],
        }

    def fetch_news(self, force_refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cached_items and (now - self._last_fetch) < self.cache_ttl_seconds:
            return {
                "fetched_at": datetime.fromtimestamp(self._last_fetch, tz=timezone.utc).isoformat(),
                "cached": True,
                "sources": self._cached_sources,
                "items": self._cached_items,
            }

        items: List[Dict[str, Any]] = []
        source_status: List[Dict[str, Any]] = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ExnessAITrader/1.0"}

        def _fetch_one(feed: Dict[str, str]):
            try:
                resp = requests.get(feed["url"], headers=headers, timeout=3.5)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                channel_items = root.findall(".//item")[:10]
                parsed_items = []
                for node in channel_items:
                    title = self._clean_html(node.findtext("title") or "")
                    if not title:
                        continue
                    link = (node.findtext("link") or "").strip()
                    desc = self._clean_html(node.findtext("description") or "")[:240]
                    pub_date = (node.findtext("pubDate") or "").strip()
                    analysis = self._score_headline(title, desc)
                    parsed_items.append(
                        {
                            "source": feed["source"],
                            "title": title,
                            "summary": desc,
                            "link": link,
                            "published_at": pub_date,
                            **analysis,
                        }
                    )
                return parsed_items, {"source": feed["source"], "status": "LIVE", "count": len(parsed_items)}
            except Exception as exc:
                logger.warning(f"News feed unavailable ({feed['source']}): {exc}")
                return [], {"source": feed["source"], "status": "UNAVAILABLE", "count": 0}

        with ThreadPoolExecutor(max_workers=len(NEWS_FEEDS)) as pool:
            for feed_items, status_entry in pool.map(_fetch_one, NEWS_FEEDS):
                items.extend(feed_items)
                source_status.append(status_entry)

        if not items:
            # Graceful offline/rate-limit fallback snapshot clearly marked as FALLBACK
            items = [
                {
                    "source": "Local Macro Monitor (Fallback)",
                    "title": "Live RSS feeds unreachable from current network; relying primarily on technical chart structure.",
                    "summary": "Check internet/firewall access if external RSS headlines are required.",
                    "link": "",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "sentiment": "NEUTRAL",
                    "raw_score": 0.0,
                    "usd_centric": False,
                    "high_impact": False,
                    "pairs": ["XAUUSD", "GBPUSD", "EURUSD"],
                }
            ]

        self._cached_items = items[:30]
        self._cached_sources = source_status
        self._last_fetch = now

        return {
            "fetched_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "cached": False,
            "sources": source_status,
            "items": self._cached_items,
        }

    def get_pair_sentiment(self, symbol: str, news_bundle: Dict[str, Any]) -> Dict[str, Any]:
        items = news_bundle.get("items", [])
        relevant = [it for it in items if symbol in it.get("pairs", [])]
        if not relevant:
            return {
                "symbol": symbol,
                "bias": "NEUTRAL",
                "score": 50.0,
                "headline_count": 0,
                "high_impact_warning": False,
                "top_headlines": [],
            }

        net_score = 0.0
        high_impact = False
        for it in relevant[:8]:
            s = float(it.get("raw_score", 0.0))
            # If headline is strictly USD bullish without mentioning Gold/GBP directly, invert for XXXUSD pairs
            title_l = it["title"].lower()
            if it.get("usd_centric") and symbol in ("XAUUSD", "GBPUSD", "EURUSD"):
                if not any(k in title_l for k in ("gold", "xau", "pound", "gbp", "euro", "eur")):
                    s = -s
            net_score += s
            if it.get("high_impact"):
                high_impact = True

        if net_score > 0.8:
            bias = "BUY"
        elif net_score < -0.8:
            bias = "SELL"
        else:
            bias = "NEUTRAL"

        # Normalize confidence 0-100
        norm_score = min(95.0, max(35.0, 50.0 + abs(net_score) * 10.0))

        return {
            "symbol": symbol,
            "bias": bias,
            "score": round(norm_score, 1),
            "net_sentiment": round(net_score, 2),
            "headline_count": len(relevant),
            "high_impact_warning": high_impact,
            "top_headlines": relevant[:4],
        }
