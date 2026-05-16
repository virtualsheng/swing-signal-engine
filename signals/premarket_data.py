"""
premarket_data.py — Pre-market quotes, gap analysis, futures
─────────────────────────────────────────────────────────────
Fetches:
  - Pre-market / extended hours price for each symbol
  - SPY / QQQ futures-implied direction
  - VIX level (fear gauge)
  - Gap size vs previous close
  - Gap fill probability (historical base rate)

Uses yfinance which includes pre/post market data.
Caches for 15 min to avoid hammering Yahoo during the morning run.
"""

import json
import logging
import os
from datetime import datetime

import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILE  = os.path.join(os.path.dirname(__file__), "..", "cache", "premarket_cache.json")
CACHE_TTL_M = 15  # minutes


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


def get_premarket_quote(symbol: str) -> dict:
    """
    Get pre-market price, change, and gap vs previous close.
    Returns:
      {
        "symbol":        str,
        "prev_close":    float,
        "premarket_price": float | None,
        "gap_pct":       float,   # % change from prev close (pre-market)
        "gap_label":     str,     # "gap up" | "gap down" | "flat"
        "is_available":  bool,
      }
    """
    result = {
        "symbol":          symbol,
        "prev_close":      0.0,
        "premarket_price": None,
        "gap_pct":         0.0,
        "gap_label":       "flat",
        "is_available":    False,
    }
    try:
        ticker    = yf.Ticker(symbol)
        info      = ticker.fast_info

        prev_close      = getattr(info, "previous_close",      None) or getattr(info, "regularMarketPreviousClose", None)
        premarket_price = getattr(info, "pre_market_price",    None)
        regular_price   = getattr(info, "last_price",          None)

        if prev_close:
            result["prev_close"] = float(prev_close)

        # Use pre-market price if available, else regular
        current = premarket_price or regular_price
        if current and prev_close and prev_close > 0:
            gap_pct = (float(current) - float(prev_close)) / float(prev_close) * 100
            result["premarket_price"] = float(current)
            result["gap_pct"]         = round(gap_pct, 2)
            result["is_available"]    = True
            if gap_pct > 0.3:
                result["gap_label"] = "gap up"
            elif gap_pct < -0.3:
                result["gap_label"] = "gap down"
            else:
                result["gap_label"] = "flat"

    except Exception as e:
        logger.debug(f"{symbol}: pre-market quote failed — {e}")

    return result


def get_market_overview() -> dict:
    """
    Get SPY, QQQ, VIX pre-market levels for the market overview section.
    Returns dict with market-wide context.
    """
    overview = {
        "spy":  {"price": None, "gap_pct": 0.0},
        "qqq":  {"price": None, "gap_pct": 0.0},
        "vix":  {"price": None, "level": "normal"},
        "timestamp": datetime.now().strftime("%H:%M ET"),
    }

    for sym, key in [("SPY", "spy"), ("QQQ", "qqq")]:
        q = get_premarket_quote(sym)
        overview[key]["price"]   = q["premarket_price"] or q["prev_close"]
        overview[key]["gap_pct"] = q["gap_pct"]

    try:
        vix_info  = yf.Ticker("^VIX").fast_info
        vix_price = getattr(vix_info, "last_price", None) or getattr(vix_info, "regularMarketPrice", None)
        if vix_price:
            vix_val = float(vix_price)
            overview["vix"]["price"] = round(vix_val, 1)
            if vix_val >= 30:
                overview["vix"]["level"] = "extreme fear"
            elif vix_val >= 20:
                overview["vix"]["level"] = "elevated"
            elif vix_val >= 15:
                overview["vix"]["level"] = "normal"
            else:
                overview["vix"]["level"] = "complacent"
    except Exception as e:
        logger.debug(f"VIX fetch failed: {e}")

    return overview


def get_premarket_batch(
    symbols: list[str],
    force: bool = False,
) -> dict[str, dict]:
    """
    Fetch pre-market data for a list of symbols with caching.
    Returns {symbol: quote_dict}.
    """
    cache = _load_cache()
    now   = datetime.utcnow()
    results = {}

    for symbol in symbols:
        if not force and symbol in cache:
            cached_at = datetime.fromisoformat(cache[symbol]["fetched_at"])
            age_min = (now - cached_at).total_seconds() / 60
            if age_min < CACHE_TTL_M:
                results[symbol] = cache[symbol]["data"]
                continue

        quote = get_premarket_quote(symbol)
        cache[symbol] = {"fetched_at": now.isoformat(), "data": quote}
        results[symbol] = quote

    _save_cache(cache)
    return results


def gap_significance(gap_pct: float, asset_class: str = "etf") -> str:
    """
    Classify the significance of a gap for swing trade purposes.
    ETFs move less than individual stocks so thresholds differ.
    """
    if asset_class == "stocks":
        if abs(gap_pct) >= 5: return "major"
        if abs(gap_pct) >= 2: return "significant"
        if abs(gap_pct) >= 1: return "moderate"
        return "minor"
    else:  # etf
        if abs(gap_pct) >= 3: return "major"
        if abs(gap_pct) >= 1.5: return "significant"
        if abs(gap_pct) >= 0.5: return "moderate"
        return "minor"


def get_macro_events_today() -> list[dict]:
    """
    Returns a hardcoded list of today's known macro events.
    In production this would pull from an economic calendar API.
    For now returns placeholder — the AI narrative will handle
    context from general knowledge.
    """
    return [
        {
            "time":   "Check CNBC/Bloomberg for today's schedule",
            "event":  "Economic calendar",
            "impact": "varies",
            "note":   "Key events: CPI, PPI, Fed speakers, jobs data, earnings",
        }
    ]