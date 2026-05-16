"""
news_fetcher.py — Yahoo Finance RSS headlines + sentiment scoring
──────────────────────────────────────────────────────────────────
Uses Yahoo Finance RSS feed directly via requests (not yfinance)
so timeouts are actually respected. Falls back to keyword scoring
instantly if Ollama is unavailable.
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

CACHE_FILE    = os.path.join(os.path.dirname(__file__), "..", "cache", "news_cache.json")
CACHE_TTL_H   = 2
MAX_HEADLINES = 5
FETCH_TIMEOUT = 6   # seconds per HTTP request — hard limit

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"


# ── Sentiment helpers ─────────────────────────────────────────────────────────

BULL_WORDS = [
    "upgrade", "buy", "outperform", "beat", "strong", "growth", "surge",
    "record", "gain", "rally", "bullish", "raised", "exceed", "positive",
    "breakthrough", "partnership", "contract", "wins", "expansion",
]
BEAR_WORDS = [
    "downgrade", "sell", "underperform", "miss", "weak", "decline", "drop",
    "loss", "bearish", "cut", "lowered", "below", "concern", "risk",
    "recall", "investigation", "lawsuit", "layoff", "warning", "disappoints",
]


def sentiment_label(score: float) -> str:
    if score >= 0.6:   return "very bullish"
    if score >= 0.25:  return "bullish"
    if score >= -0.25: return "neutral"
    if score >= -0.6:  return "bearish"
    return "very bearish"


def sentiment_emoji(score: float) -> str:
    if score >= 0.6:   return "🟢🟢"
    if score >= 0.25:  return "🟢"
    if score >= -0.25: return "⚪"
    if score >= -0.6:  return "🔴"
    return "🔴🔴"


def _keyword_sentiment(text: str) -> float:
    text_lower = text.lower()
    bull  = sum(1 for w in BULL_WORDS if w in text_lower)
    bear  = sum(1 for w in BEAR_WORDS if w in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)


def _quick_summary(headlines: list, score: float) -> str:
    if not headlines:
        return "No recent news."
    top = headlines[0].get("title", "")[:80]
    return f"{sentiment_label(score).title()} tone. Latest: {top}"


# ── RSS news fetcher ──────────────────────────────────────────────────────────

def _fetch_rss_headlines(symbol: str) -> list[dict]:
    """
    Fetch headlines from Yahoo Finance RSS feed.
    Uses requests with a hard timeout — never hangs.
    Returns list of {title, publisher, age_hours}.
    """
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0"
        })
        if resp.status_code != 200:
            return []

        root  = ET.fromstring(resp.text)
        items = root.findall(".//item")
        now   = datetime.utcnow()
        headlines = []
        for item in items[:MAX_HEADLINES]:
            title = item.findtext("title", "").strip()
            pub   = item.findtext("pubDate", "")
            source = item.findtext("source", "Yahoo Finance")
            age_h = 99.0
            if pub:
                try:
                    from email.utils import parsedate_to_datetime
                    dt    = parsedate_to_datetime(pub).replace(tzinfo=None)
                    age_h = (now - dt).total_seconds() / 3600
                except Exception:
                    pass
            if title:
                headlines.append({
                    "title":     title,
                    "publisher": source,
                    "age_hours": round(age_h, 1),
                })
        return headlines

    except requests.Timeout:
        logger.debug(f"{symbol}: RSS fetch timed out after {FETCH_TIMEOUT}s")
        return []
    except Exception as e:
        logger.debug(f"{symbol}: RSS fetch failed — {e}")
        return []


# ── Ollama sentiment (optional upgrade) ───────────────────────────────────────

def _ollama_sentiment(headlines_text: str, symbol: str) -> dict | None:
    prompt = f"""Rate the overall news sentiment for {symbol} based on these headlines.

Headlines:
{headlines_text}

Respond ONLY with valid JSON, no markdown:
{{
  "sentiment_score": <float -1.0 to 1.0>,
  "sentiment_label": "<very bearish|bearish|neutral|bullish|very bullish>",
  "summary": "<1 sentence describing the key news theme>",
  "catalyst": "<main catalyst if any, or 'none'>"
}}"""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json().get("response", "").strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        result["sentiment_score"] = float(
            max(-1.0, min(1.0, result.get("sentiment_score", 0.0)))
        )
        return result
    except Exception:
        return None


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch_news(symbol: str, force: bool = False) -> dict:
    """
    Fetch and score news for a symbol. Always returns quickly.
    Uses RSS (6s timeout) → keyword scoring → optional Ollama upgrade.
    """
    cache = _load_cache()
    now   = datetime.utcnow()

    if not force and symbol in cache:
        try:
            cached_at = datetime.fromisoformat(cache[symbol]["fetched_at"])
            if (now - cached_at).total_seconds() / 3600 < CACHE_TTL_H:
                return cache[symbol]["data"]
        except Exception:
            pass

    result = {
        "symbol":            symbol,
        "headlines":         [],
        "sentiment":         0.0,
        "sentiment_label":   "neutral",
        "sentiment_summary": "No recent news.",
        "catalyst":          "none",
    }

    headlines = _fetch_rss_headlines(symbol)
    result["headlines"] = headlines

    if headlines:
        headlines_text = "\n".join(
            f"- {h['title']} ({h['publisher']}, {h['age_hours']:.0f}h ago)"
            for h in headlines
        )
        # Try Ollama; fall back to keyword instantly
        ai_result = _ollama_sentiment(headlines_text, symbol)
        if ai_result:
            result["sentiment"]         = ai_result.get("sentiment_score", 0.0)
            result["sentiment_label"]   = ai_result.get("sentiment_label", "neutral")
            result["sentiment_summary"] = ai_result.get("summary", "")
            result["catalyst"]          = ai_result.get("catalyst", "none")
        else:
            score = _keyword_sentiment(headlines_text)
            result["sentiment"]         = score
            result["sentiment_label"]   = sentiment_label(score)
            result["sentiment_summary"] = _quick_summary(headlines, score)

    cache[symbol] = {"fetched_at": now.isoformat(), "data": result}
    _save_cache(cache)
    return result


def fetch_news_batch(symbols: list[str], force: bool = False) -> dict[str, dict]:
    """
    Fetch news for multiple symbols in parallel.
    Hard 30-second total budget — never hangs the morning report.
    """
    import concurrent.futures
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_news, sym, force): sym for sym in symbols}
        done, _ = concurrent.futures.wait(futures, timeout=30)
        for future in done:
            sym = futures[future]
            try:
                results[sym] = future.result()
            except Exception:
                pass
        # Fill in any that timed out
        for future, sym in futures.items():
            if sym not in results:
                logger.debug(f"{sym}: news skipped (30s budget exceeded)")
                results[sym] = {
                    "symbol": sym, "headlines": [],
                    "sentiment": 0.0, "sentiment_label": "neutral",
                    "sentiment_summary": "Fetch skipped (timeout).",
                    "catalyst": "none",
                }
    return results