"""
premarket_data.py — Pre-market quotes, gap analysis, market overview
─────────────────────────────────────────────────────────────────────
Fetches:
  - Pre-market / extended hours price for each symbol
  - SPY / QQQ pre-market gap vs prior close
  - VIX level
  - Gap size classification

Uses yfinance with prepost=True (1m bars) for reliable pre-market prices.
fast_info.pre_market_price is unreliable — it frequently returns None
even when pre-market is active. 1-minute bars with prepost=True are
the correct approach.

Caches for 15 min to avoid hammering Yahoo during the morning run.
"""

import json
import logging
import os
from datetime import datetime, timedelta

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


def _get_premarket_price_and_prev(symbol: str) -> tuple[float | None, float | None]:
    """
    Returns (premarket_price, prev_regular_close).

    Strategy:
    1. Fetch 2d of 1-minute bars with prepost=True
    2. Split into regular-session bars and pre-market bars
    3. premarket_price = last bar before 9:30 AM today ET
    4. prev_close      = last bar of the prior regular session (<=4:00 PM yesterday)

    Falls back to fast_info if bar fetch fails.
    """
    import pytz
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)

    try:
        ticker = yf.Ticker(symbol)

        # ── Try 1-min bars with pre/post market ──────────────────────────
        hist = ticker.history(period="2d", interval="1m", prepost=True)

        if hist is not None and not hist.empty:
            # Ensure timezone-aware index
            if hist.index.tz is None:
                hist.index = hist.index.tz_localize("UTC").tz_convert(et)
            else:
                hist.index = hist.index.tz_convert(et)

            today = now_et.date()

            # Regular session: 9:30–16:00 ET
            regular_mask = (
                (hist.index.time >= __import__("datetime").time(9, 30)) &
                (hist.index.time <= __import__("datetime").time(16, 0))
            )
            # Pre-market today: before 9:30 AM
            premarket_mask = (
                hist.index.date == today
            ) & (
                hist.index.time < __import__("datetime").time(9, 30)
            )

            premarket_bars = hist[premarket_mask]
            regular_bars   = hist[regular_mask & (hist.index.date < today)]

            premarket_price = None
            prev_close      = None

            if not premarket_bars.empty:
                premarket_price = float(premarket_bars["Close"].iloc[-1])

            if not regular_bars.empty:
                prev_close = float(regular_bars["Close"].iloc[-1])
            else:
                # Fallback: fast_info previous_close
                info = ticker.fast_info
                prev_close = (
                    getattr(info, "regularMarketPreviousClose", None) or
                    getattr(info, "previous_close", None)
                )
                if prev_close:
                    prev_close = float(prev_close)

            return premarket_price, prev_close

    except Exception as e:
        logger.debug(f"{symbol}: bar-based premarket fetch failed — {e}")

    # ── Fallback: fast_info ───────────────────────────────────────────────
    try:
        info  = yf.Ticker(symbol).fast_info
        price = getattr(info, "pre_market_price", None)
        prev  = (
            getattr(info, "regularMarketPreviousClose", None) or
            getattr(info, "previous_close", None)
        )
        return (float(price) if price else None,
                float(prev)  if prev  else None)
    except Exception as e:
        logger.debug(f"{symbol}: fast_info fallback failed — {e}")

    return None, None


def get_premarket_quote(symbol: str) -> dict:
    """
    Get pre-market price, change, and gap vs previous close.
    Returns:
      {
        "symbol":          str,
        "prev_close":      float,
        "premarket_price": float | None,
        "gap_pct":         float,
        "gap_label":       str,    # "gap up" | "gap down" | "flat"
        "is_available":    bool,
      }
    """
    import pytz
    now_et    = datetime.now(pytz.timezone("America/New_York"))
    hour      = now_et.hour
    minute    = now_et.minute

    if 4 <= hour < 9 or (hour == 9 and minute < 30):
        data_type = "pre-market"
    elif (hour == 9 and minute >= 30) or (9 < hour < 16):
        data_type = "intraday"
    elif 16 <= hour < 20:
        data_type = "after-hours"
    else:
        data_type = "overnight"

    result = {
        "symbol":          symbol,
        "prev_close":      0.0,
        "premarket_price": None,
        "gap_pct":         0.0,
        "gap_label":       "flat",
        "is_available":    False,
        "data_type":       data_type,
    }

    premarket_price, prev_close = _get_premarket_price_and_prev(symbol)

    if prev_close:
        result["prev_close"] = round(float(prev_close), 4)

    if premarket_price and prev_close and float(prev_close) > 0:
        gap_pct = (float(premarket_price) - float(prev_close)) / float(prev_close) * 100
        result["premarket_price"] = round(float(premarket_price), 4)
        result["gap_pct"]         = round(gap_pct, 2)
        result["is_available"]    = True
        if gap_pct > 0.3:
            result["gap_label"] = "gap up"
        elif gap_pct < -0.3:
            result["gap_label"] = "gap down"
        else:
            result["gap_label"] = "flat"

    return result


def get_market_overview() -> dict:
    """
    Get SPY, QQQ, VIX pre-market levels for the market overview section.
    """
    overview = {
        "spy":       {"price": None, "gap_pct": 0.0},
        "qqq":       {"price": None, "gap_pct": 0.0},
        "vix":       {"price": None, "level": "normal"},
        "timestamp": datetime.now().strftime("%H:%M ET"),
    }

    for sym, key in [("SPY", "spy"), ("QQQ", "qqq")]:
        q = get_premarket_quote(sym)
        overview[key]["price"]   = q["premarket_price"] or q["prev_close"]
        overview[key]["gap_pct"] = q["gap_pct"]
        logger.info(
            f"  {sym}: prev_close={q['prev_close']:.2f}  "
            f"premarket={q['premarket_price']}  "
            f"gap={q['gap_pct']:+.2f}%  "
            f"[{q['data_type']}]"
        )

    try:
        vix_info  = yf.Ticker("^VIX").fast_info
        vix_price = (
            getattr(vix_info, "last_price",          None) or
            getattr(vix_info, "regularMarketPrice",  None)
        )
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
    cache   = _load_cache()
    now     = datetime.utcnow()
    results = {}

    for symbol in symbols:
        if not force and symbol in cache:
            try:
                cached_at = datetime.fromisoformat(cache[symbol]["fetched_at"])
                age_min   = (now - cached_at).total_seconds() / 60
                if age_min < CACHE_TTL_M:
                    results[symbol] = cache[symbol]["data"]
                    continue
            except Exception:
                pass

        quote            = get_premarket_quote(symbol)
        cache[symbol]    = {"fetched_at": now.isoformat(), "data": quote}
        results[symbol]  = quote

    _save_cache(cache)
    return results


def gap_significance(gap_pct: float, asset_class: str = "etf") -> str:
    """Classify the significance of a gap for swing trade purposes."""
    if asset_class == "stocks":
        if abs(gap_pct) >= 5:   return "major"
        if abs(gap_pct) >= 2:   return "significant"
        if abs(gap_pct) >= 1:   return "moderate"
        return "minor"
    else:   # etf
        if abs(gap_pct) >= 3:   return "major"
        if abs(gap_pct) >= 1.5: return "significant"
        if abs(gap_pct) >= 0.5: return "moderate"
        return "minor"


def get_macro_events_today() -> list[dict]:
    """Placeholder for economic calendar integration."""
    return [
        {
            "time":   "Check CNBC/Bloomberg for today's schedule",
            "event":  "Economic calendar",
            "impact": "varies",
            "note":   "Key events: CPI, PPI, Fed speakers, jobs data, earnings",
        }
    ]