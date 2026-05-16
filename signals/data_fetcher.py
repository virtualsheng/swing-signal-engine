"""
data_fetcher.py — Yahoo Finance daily data with local caching
─────────────────────────────────────────────────────────────
Fetches 250 days of daily OHLCV for each symbol using yfinance.
Caches results in cache/price_cache.json to avoid redundant fetches
within the same day (morning + EOD runs both use the same data).
"""

import json
import logging
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILE    = os.path.join(os.path.dirname(__file__), "..", "cache", "price_cache.json")
CACHE_BARS    = 250   # enough for SMA200 + buffer
CACHE_TTL_MIN = 60    # re-fetch if cache is older than 60 min


def _cache_key(symbol: str) -> str:
    return symbol.upper()


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


def _df_to_cache(df: pd.DataFrame) -> dict:
    """
    Serialize a DataFrame to a JSON-safe dict.
    Converts the DatetimeIndex to ISO strings so json.dump never
    encounters a Timestamp object (which is not JSON-serializable).
    """
    out = {}
    for col in df.columns:
        out[col] = {
            k.isoformat() if hasattr(k, "isoformat") else str(k): float(v)
            for k, v in df[col].items()
        }
    return out


def _df_from_cache(data: dict) -> pd.DataFrame:
    """Reconstruct a DataFrame from the ISO-string-keyed cache dict."""
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def fetch_daily_bars(symbol: str, force: bool = False) -> pd.DataFrame | None:
    """
    Fetch 250 days of daily OHLCV for a symbol.
    Returns a DataFrame indexed by datetime with columns:
        open, high, low, close, volume
    Returns None on failure.
    """
    cache = _load_cache()
    key   = _cache_key(symbol)
    now   = datetime.utcnow()

    if not force and key in cache:
        try:
            cached_at = datetime.fromisoformat(cache[key]["fetched_at"])
            age_min   = (now - cached_at).total_seconds() / 60
            if age_min < CACHE_TTL_MIN:
                df = _df_from_cache(cache[key]["data"])
                if df is not None and len(df) >= 30:
                    return df
        except Exception:
            pass  # stale or corrupt cache — fall through to re-fetch

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{CACHE_BARS}d", interval="1d", auto_adjust=True)
        if df is None or len(df) < 30:
            logger.warning(f"{symbol}: insufficient data "
                           f"({len(df) if df is not None else 0} bars)")
            return None

        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].dropna()

        # Serialize with string-keyed index to avoid Timestamp JSON error
        cache[key] = {
            "fetched_at": now.isoformat(),
            "data":       _df_to_cache(df),
        }
        _save_cache(cache)

        return df

    except Exception as e:
        logger.warning(f"{symbol}: fetch failed — {e}")
        return None


def fetch_batch(symbols: list[str], force: bool = False) -> dict[str, pd.DataFrame]:
    """
    Fetch daily bars for a list of symbols.
    Returns dict of {symbol: DataFrame}, skipping failures silently.
    """
    results = {}
    for sym in symbols:
        df = fetch_daily_bars(sym, force=force)
        if df is not None:
            results[sym] = df
        else:
            logger.info(f"Skipping {sym} — no data available")
    return results


def get_spy_closes(n: int = 20) -> list[float]:
    """Return the last n SPY daily closes for regime detection."""
    df = fetch_daily_bars("SPY")
    if df is None or len(df) < n:
        return []
    return df["close"].tail(n).tolist()