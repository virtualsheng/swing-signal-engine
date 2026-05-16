"""
opening_range.py — Opening range analysis + signal confirmation
────────────────────────────────────────────────────────────────
Run at 9:50 AM after the first 15 minutes of real price action.

For each symbol with an active EOD signal, determines whether
the opening price action confirms or invalidates the signal.

Confirmation criteria:
  BUY signal confirmed:
    - Price opened above or within 0.5% of previous close (no gap down)
    - First 15-min candle is bullish (close > open)
    - Volume is above average (>1.0x)
    - Price is above SMA50

  SELL signal confirmed:
    - Price opened below or within 0.5% of previous close (no gap up)
    - First 15-min candle is bearish (close < open)
    - Price is below SMA50

  Signal invalidated:
    - BUY invalidated: gap down >1.5% on high volume
    - SELL invalidated: gap up >1.5% on high volume
    - Either: price action contradicts signal direction

Returns per symbol:
  {
    "symbol":          str,
    "eod_signal":      str,    # from previous night
    "confirmed":       bool,
    "invalidated":     bool,
    "action":          str,    # "EXECUTE NOW" | "WAIT" | "STAND DOWN"
    "entry_price":     float,  # suggested entry price
    "stop_price":      float,  # suggested stop
    "opening_range_high": float,
    "opening_range_low":  float,
    "open_volume_ratio":  float,
    "candle_direction":   str,  # "bullish" | "bearish" | "doji"
    "gap_pct":            float,
    "reasoning":          str,
  }
"""

import logging
from datetime import datetime, time as dtime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

ORB_MINUTES = 15  # opening range = first 15 min


def get_opening_range(symbol: str) -> dict | None:
    """
    Fetch today's intraday 1-min bars and compute the opening range
    (high/low of first 15 minutes).
    Returns None if data unavailable.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m", prepost=False)
        if df is None or len(df) < 5:
            return None

        df.columns = [c.lower() for c in df.columns]
        df.index   = pd.to_datetime(df.index)

        # Filter to regular market hours today
        today = datetime.now().date()
        df    = df[df.index.date == today]

        if len(df) < 3:
            return None

        # Opening range = first ORB_MINUTES minutes
        market_open = df.index[0]
        or_end      = market_open + timedelta(minutes=ORB_MINUTES)
        or_bars     = df[df.index <= or_end]

        if len(or_bars) == 0:
            return None

        or_high = float(or_bars["high"].max())
        or_low  = float(or_bars["low"].min())
        or_open = float(or_bars["open"].iloc[0])
        or_close = float(or_bars["close"].iloc[-1])
        or_vol  = float(or_bars["volume"].sum())

        # Current price (latest available)
        current_price = float(df["close"].iloc[-1])

        return {
            "or_high":         round(or_high, 2),
            "or_low":          round(or_low, 2),
            "or_open":         round(or_open, 2),
            "or_close":        round(or_close, 2),
            "or_volume":       int(or_vol),
            "current_price":   round(current_price, 2),
            "bars_available":  len(df),
        }

    except Exception as e:
        logger.debug(f"{symbol}: opening range fetch failed — {e}")
        return None


def confirm_signal(
    symbol: str,
    eod_signal: str,
    eod_conviction: int,
    prev_close: float,
    avg_daily_volume: float,
    above_sma50: bool,
    above_sma200: bool,
    scorecard: dict,
    account_name: str = "",
    acct_value: float = 0,
    suggested_usd: float = 0,
    shares_held: int = 0,
    avg_cost: float = 0,
) -> dict:
    """
    Confirm or invalidate an EOD signal using opening range data.
    Returns a confirmation dict with action recommendation.
    """
    result = {
        "symbol":             symbol,
        "account":            account_name,
        "eod_signal":         eod_signal,
        "confirmed":          False,
        "invalidated":        False,
        "action":             "WAIT",
        "entry_price":        0.0,
        "stop_price":         0.0,
        "target_price":       0.0,
        "opening_range_high": 0.0,
        "opening_range_low":  0.0,
        "open_volume_ratio":  0.0,
        "candle_direction":   "unknown",
        "gap_pct":            0.0,
        "reasoning":          "Opening range data unavailable.",
        "suggested_usd":      suggested_usd,
        "acct_value":         acct_value,
        "shares_held":        shares_held,
        "avg_cost":           avg_cost,
    }

    or_data = get_opening_range(symbol)
    if not or_data:
        result["reasoning"] = "Could not fetch opening range data — check manually."
        return result

    or_high   = or_data["or_high"]
    or_low    = or_data["or_low"]
    or_open   = or_data["or_open"]
    or_close  = or_data["or_close"]
    or_vol    = or_data["or_volume"]
    current   = or_data["current_price"]

    result["opening_range_high"] = or_high
    result["opening_range_low"]  = or_low

    # Gap vs previous close
    gap_pct = (or_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
    result["gap_pct"] = round(gap_pct, 2)

    # Opening candle direction
    if or_close > or_open * 1.001:
        candle = "bullish"
    elif or_close < or_open * 0.999:
        candle = "bearish"
    else:
        candle = "doji"
    result["candle_direction"] = candle

    # Volume ratio vs expected (ORB_MINUTES / 390 of daily volume)
    expected_vol = avg_daily_volume * (ORB_MINUTES / 390) if avg_daily_volume > 0 else or_vol
    vol_ratio    = or_vol / expected_vol if expected_vol > 0 else 1.0
    result["open_volume_ratio"] = round(vol_ratio, 2)

    # ── BUY signal confirmation ───────────────────────────────────────────────
    if eod_signal in ("BUY", "STRONG_BUY"):
        or_range = or_high - or_low if or_high > or_low else or_low * 0.01

        # Invalidation conditions
        if gap_pct < -1.5 and vol_ratio > 1.5:
            result["invalidated"] = True
            result["action"]      = "STAND DOWN"
            result["reasoning"]   = (
                f"BUY signal invalidated: gap down {gap_pct:.1f}% on "
                f"{vol_ratio:.1f}x volume. Signal likely broken."
            )
            return result

        if candle == "bearish" and not above_sma50:
            result["invalidated"] = True
            result["action"]      = "STAND DOWN"
            result["reasoning"]   = (
                f"BUY signal invalidated: bearish opening candle below SMA50. "
                f"Wait for a better entry or skip today."
            )
            return result

        # Confirmation conditions
        bullish_candle = candle == "bullish"
        price_ok       = gap_pct > -0.5  # didn't gap down materially
        volume_ok      = vol_ratio >= 1.0
        sma_ok         = above_sma50

        confirmed = (bullish_candle and price_ok and volume_ok) or \
                    (bullish_candle and sma_ok and price_ok)

        if confirmed:
            # Entry: current price or OR breakout (just above OR high)
            entry  = round(max(current, or_high * 1.001), 2)
            stop   = round(or_low * 0.998, 2)
            risk   = entry - stop
            target = round(entry + risk * 2.0, 2)  # 2:1 reward

            result["confirmed"]    = True
            result["action"]       = "EXECUTE NOW"
            result["entry_price"]  = entry
            result["stop_price"]   = stop
            result["target_price"] = target
            result["reasoning"]    = (
                f"BUY confirmed: {candle} opening candle, "
                f"gap {gap_pct:+.1f}%, volume {vol_ratio:.1f}x avg. "
                f"{'Above SMA50 ✓' if above_sma50 else ''} "
                f"Entry ≤${entry:.2f}, stop ${stop:.2f}, "
                f"target ${target:.2f} (2:1 R/R)."
            )
        else:
            result["action"]    = "WAIT"
            result["reasoning"] = (
                f"BUY signal present but not yet confirmed: "
                f"candle={candle}, gap={gap_pct:+.1f}%, vol={vol_ratio:.1f}x. "
                f"Watch for price to hold above OR low ${or_low:.2f} "
                f"and break above ${or_high:.2f} with volume."
            )

    # ── SELL signal confirmation ──────────────────────────────────────────────
    elif eod_signal in ("SELL", "STRONG_SELL"):
        # Invalidation
        if gap_pct > 1.5 and vol_ratio > 1.5:
            result["invalidated"] = True
            result["action"]      = "STAND DOWN"
            result["reasoning"]   = (
                f"SELL signal invalidated: gap up {gap_pct:.1f}% on "
                f"{vol_ratio:.1f}x volume. Don't sell into strength."
            )
            return result

        bearish_candle = candle == "bearish"
        price_ok       = gap_pct < 0.5
        volume_ok      = vol_ratio >= 0.8

        confirmed = bearish_candle and price_ok

        if confirmed:
            result["confirmed"]   = True
            result["action"]      = "EXECUTE NOW"
            result["entry_price"] = current
            result["reasoning"]   = (
                f"SELL confirmed: {candle} opening candle, "
                f"gap {gap_pct:+.1f}%, vol {vol_ratio:.1f}x. "
                f"Reduce position at or near ${current:.2f}."
            )
        else:
            result["action"]    = "WAIT"
            result["reasoning"] = (
                f"SELL signal present but opening not confirming yet: "
                f"candle={candle}, gap={gap_pct:+.1f}%. "
                f"Watch — if price rolls over below ${or_low:.2f}, execute."
            )

    # ── HOLD — no action ─────────────────────────────────────────────────────
    else:
        result["action"]    = "HOLD"
        result["reasoning"] = f"HOLD signal — no trade needed. Current: ${current:.2f}"

    return result


def get_avg_volume(symbol: str) -> float:
    """Get 20-day average daily volume for volume ratio calc."""
    try:
        df = yf.Ticker(symbol).history(period="30d", interval="1d")
        if df is not None and len(df) >= 10:
            return float(df["Volume"].tail(20).mean())
    except Exception:
        pass
    return 0.0