"""
signal_engine.py — Full technical scorecard
─────────────────────────────────────────────
Returns a complete per-symbol technical breakdown:
  EMA 2/3/5       crossover + alignment
  RSI(14)         level + label
  MACD            crossover + histogram direction
  SMA 50/200      price position + distance %
  ATR(14)         volatility measure
  Volume ratio    vs 20-day average
  52-week range   position %
  1d / 5d / 20d   price change %
  Signal          BUY | SELL | HOLD
  Conviction      0–100
  Scorecard       dict of all indicator pass/fail for display
"""

import logging
from datetime import datetime

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series):
    fast   = _ema(series, 12)
    slow   = _ema(series, 26)
    macd   = fast - slow
    signal = _ema(macd, 9)
    hist   = macd - signal
    return macd, signal, hist


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return float(atr.iloc[-1])


def _rsi_label(rsi: float) -> str:
    if rsi >= 80: return "extremely overbought"
    if rsi >= 70: return "overbought"
    if rsi >= 60: return "mildly overbought"
    if rsi >= 40: return "neutral"
    if rsi >= 30: return "mildly oversold"
    if rsi >= 20: return "oversold"
    return "extremely oversold"


def get_technical_signal(symbol: str, df: pd.DataFrame) -> dict:
    """
    Full technical scorecard. Returns signal + complete indicator breakdown.
    """
    empty = {
        "signal": "HOLD", "conviction": 50, "bear_score": 0,
        "ema_cross": False, "rsi": 50.0, "rsi_label": "neutral",
        "above_sma50": False, "above_sma200": False,
        "vol_ratio": 1.0, "price": 0.0,
        "date": datetime.today().strftime("%Y-%m-%d"),
        "reason": "insufficient data",
        "scorecard": {},
    }
    if df is None or len(df) < 30:
        return empty

    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)

    # ── Indicators ────────────────────────────────────────────────────────────
    ema2 = _ema(close, 2)
    ema3 = _ema(close, 3)
    ema5 = _ema(close, 5)
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi_s  = _rsi(close, 14)
    macd_line, macd_sig, macd_hist = _macd(close)
    vol_sma = volume.rolling(20).mean()
    high52  = df["high"].astype(float).rolling(252).max()
    low52   = df["low"].astype(float).rolling(252).min()

    # ── Latest values ─────────────────────────────────────────────────────────
    price        = float(close.iloc[-1])
    cur_rsi      = float(rsi_s.iloc[-1])
    cur_ema2     = float(ema2.iloc[-1]);  prev_ema2 = float(ema2.iloc[-2])
    cur_ema3     = float(ema3.iloc[-1]);  prev_ema3 = float(ema3.iloc[-2])
    cur_ema5     = float(ema5.iloc[-1]);  prev_ema5 = float(ema5.iloc[-2])
    cur_sma50    = float(sma50.iloc[-1])  if len(df) >= 50  else price
    cur_sma200   = float(sma200.iloc[-1]) if len(df) >= 200 else price
    cur_macd     = float(macd_line.iloc[-1]); prev_macd    = float(macd_line.iloc[-2])
    cur_macd_sig = float(macd_sig.iloc[-1]);  prev_macd_sig= float(macd_sig.iloc[-2])
    cur_hist     = float(macd_hist.iloc[-1]); prev_hist    = float(macd_hist.iloc[-2])
    cur_vol      = float(volume.iloc[-1])
    avg_vol      = float(vol_sma.iloc[-1]) if not pd.isna(vol_sma.iloc[-1]) else cur_vol
    vol_ratio    = cur_vol / avg_vol if avg_vol > 0 else 1.0
    h52          = float(high52.iloc[-1]) if not pd.isna(high52.iloc[-1]) else price
    l52          = float(low52.iloc[-1])  if not pd.isna(low52.iloc[-1])  else price
    range52_pct  = ((price - l52) / (h52 - l52) * 100) if h52 > l52 else 50.0

    # Price changes
    chg_1d  = (price / float(close.iloc[-2])  - 1) * 100 if len(close) >= 2   else 0.0
    chg_5d  = (price / float(close.iloc[-6])  - 1) * 100 if len(close) >= 6   else 0.0
    chg_20d = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21  else 0.0

    # SMA distances
    dist_sma50  = (price / cur_sma50  - 1) * 100
    dist_sma200 = (price / cur_sma200 - 1) * 100

    # ATR
    try:
        atr     = _atr(df, 14)
        atr_pct = atr / price * 100
    except Exception:
        atr     = 0.0
        atr_pct = 0.0

    above_sma50  = price > cur_sma50
    above_sma200 = price > cur_sma200

    # ── Crossover detection ───────────────────────────────────────────────────
    ema_bull_cross   = (cur_ema2 > cur_ema3 > cur_ema5) and not (prev_ema2 > prev_ema3 > prev_ema5)
    ema_bear_cross   = (cur_ema2 < cur_ema3 < cur_ema5) and not (prev_ema2 < prev_ema3 < prev_ema5)
    ema_aligned_bull = cur_ema2 > cur_ema3 > cur_ema5
    ema_aligned_bear = cur_ema2 < cur_ema3 < cur_ema5
    macd_bull_cross  = (cur_macd > cur_macd_sig) and (prev_macd <= prev_macd_sig)
    macd_bear_cross  = (cur_macd < cur_macd_sig) and (prev_macd >= prev_macd_sig)
    macd_bull        = cur_macd > cur_macd_sig
    hist_rising      = cur_hist > prev_hist

    # ── Scoring ───────────────────────────────────────────────────────────────
    bull_score = 0.0
    bear_score = 0.0
    reasons    = []

    if ema_bull_cross:
        bull_score += 2; reasons.append("EMA bull cross")
    elif ema_aligned_bull:
        bull_score += 1; reasons.append("EMA aligned bull")
    if ema_bear_cross:
        bear_score += 2; reasons.append("EMA bear cross")
    elif ema_aligned_bear:
        bear_score += 1; reasons.append("EMA aligned bear")

    if cur_rsi < 35:
        bull_score += 1; reasons.append(f"RSI oversold {cur_rsi:.0f}")
    elif cur_rsi > 65:
        bear_score += 1; reasons.append(f"RSI overbought {cur_rsi:.0f}")

    if macd_bull_cross:
        bull_score += 1; reasons.append("MACD bull cross")
    elif macd_bull:
        bull_score += 0.5
    if macd_bear_cross:
        bear_score += 1; reasons.append("MACD bear cross")
    elif not macd_bull:
        bear_score += 0.5

    if above_sma50:
        bull_score += 1; reasons.append("above SMA50")
    else:
        bear_score += 1; reasons.append("below SMA50")
    if above_sma200:
        bull_score += 1; reasons.append("above SMA200")
    else:
        bear_score += 1; reasons.append("below SMA200")

    if vol_ratio > 1.5:
        if bull_score > bear_score:
            bull_score += 0.5; reasons.append(f"vol surge {vol_ratio:.1f}x")
        else:
            bear_score += 0.5; reasons.append(f"vol surge {vol_ratio:.1f}x")

    net = bull_score - bear_score
    if net >= 2:
        signal         = "BUY"
        raw_conviction = min(100, 50 + net * 8)
    elif net <= -2:
        signal         = "SELL"
        raw_conviction = min(100, 50 + abs(net) * 8)
    else:
        signal         = "HOLD"
        raw_conviction = max(20, 50 - abs(net) * 5)

    conviction = int(raw_conviction)
    if signal == "BUY":
        if above_sma200:   conviction = min(100, conviction + 5)
        if vol_ratio > 1.5: conviction = min(100, conviction + 3)
        if cur_rsi > 75:   conviction = max(0,   conviction - 10)
    elif signal == "SELL":
        if not above_sma200: conviction = min(100, conviction + 5)
        if cur_rsi < 25:     conviction = max(0,   conviction - 10)

    # ── Full scorecard dict (for display in report) ───────────────────────────
    scorecard = {
        # EMA
        "ema2":             round(cur_ema2, 2),
        "ema3":             round(cur_ema3, 2),
        "ema5":             round(cur_ema5, 2),
        "ema_aligned_bull": ema_aligned_bull,
        "ema_aligned_bear": ema_aligned_bear,
        "ema_bull_cross":   ema_bull_cross,
        "ema_bear_cross":   ema_bear_cross,
        "ema_label":        ("🟢 bull cross" if ema_bull_cross else
                             "🔴 bear cross" if ema_bear_cross else
                             "🟢 aligned bull" if ema_aligned_bull else
                             "🔴 aligned bear" if ema_aligned_bear else
                             "⚪ mixed"),
        # RSI
        "rsi":              round(cur_rsi, 1),
        "rsi_label":        _rsi_label(cur_rsi),
        "rsi_color":        ("green" if cur_rsi < 40 else
                             "red"   if cur_rsi > 65 else "neutral"),
        # MACD
        "macd":             round(cur_macd, 4),
        "macd_signal":      round(cur_macd_sig, 4),
        "macd_hist":        round(cur_hist, 4),
        "macd_bull":        macd_bull,
        "macd_bull_cross":  macd_bull_cross,
        "macd_bear_cross":  macd_bear_cross,
        "hist_rising":      hist_rising,
        "macd_label":       ("🟢 bull cross" if macd_bull_cross else
                             "🔴 bear cross" if macd_bear_cross else
                             "🟢 above signal" if macd_bull else
                             "🔴 below signal"),
        # SMA
        "sma50":            round(cur_sma50, 2),
        "sma200":           round(cur_sma200, 2),
        "above_sma50":      above_sma50,
        "above_sma200":     above_sma200,
        "dist_sma50_pct":   round(dist_sma50, 2),
        "dist_sma200_pct":  round(dist_sma200, 2),
        # Volume
        "vol_ratio":        round(vol_ratio, 2),
        "vol_label":        ("surge" if vol_ratio > 2 else
                             "high"  if vol_ratio > 1.5 else
                             "avg"   if vol_ratio > 0.8 else "low"),
        # ATR / volatility
        "atr":              round(atr, 2),
        "atr_pct":          round(atr_pct, 2),
        # 52-week range
        "high_52w":         round(h52, 2),
        "low_52w":          round(l52, 2),
        "range52_pct":      round(range52_pct, 1),
        # Price changes
        "chg_1d":           round(chg_1d, 2),
        "chg_5d":           round(chg_5d, 2),
        "chg_20d":          round(chg_20d, 2),
        # Score breakdown
        "bull_score":       round(bull_score, 1),
        "bear_score_val":   round(bear_score, 1),
        "net_score":        round(net, 1),
    }

    return {
        "signal":       signal,
        "conviction":   conviction,
        "bear_score":   int(round(bear_score)),
        "ema_cross":    ema_bull_cross or ema_bear_cross,
        "rsi":          round(cur_rsi, 1),
        "rsi_label":    _rsi_label(cur_rsi),
        "above_sma50":  above_sma50,
        "above_sma200": above_sma200,
        "vol_ratio":    round(vol_ratio, 2),
        "price":        round(price, 2),
        "chg_1d":       round(chg_1d, 2),
        "chg_5d":       round(chg_5d, 2),
        "date":         df.index[-1].strftime("%Y-%m-%d"),
        "reason":       " | ".join(reasons) if reasons else "no strong signal",
        "scorecard":    scorecard,
    }