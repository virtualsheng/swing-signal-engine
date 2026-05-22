"""
signals/ai_engine.py — AI grading + narratives for swing signal engine
───────────────────────────────────────────────────────────────────────
Provider priority (auto-fallback):
  1. Gemini (gemini-2.0-flash-lite) — free 1,500 req/day
  2. Groq  (qwen3-32b)              — free 14,400 req/day
  3. Ollama (local)                 — offline fallback

Set in .env:
  GEMINI_API_KEY=AIza...   # aistudio.google.com
  GROQ_API_KEY=gsk_...     # console.groq.com
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_router import llm_call, llm_call_text, llm_available, llm_provider_status

logger = logging.getLogger(__name__)

# ── Grade cache — keyed by symbol, shared across all accounts ─────────────────
_grade_cache: dict[str, dict] = {}


def clear_grade_cache():
    _grade_cache.clear()


def check_ollama_available() -> bool:
    """Backward-compatible name — checks ANY provider (Gemini/Groq/Ollama)."""
    status = llm_provider_status()
    order  = status["active_order"]
    for p in order:
        s = status[p]
        mark = "✓" if s["configured"] else "–"
        logger.info(f"  {mark} {p.upper():<8} {s['model']}")
    available = llm_available()
    logger.info(
        f"AI: {'ready — ' + ' → '.join(order) if available else 'unavailable — fallback mode'}"
    )
    return available


def grade_swing_setup(
    symbol: str,
    signal: str,
    conviction: int,
    price: float,
    rsi: float,
    above_sma50: bool,
    above_sma200: bool,
    vol_ratio: float,
    ema_cross: bool,
    reason: str,
    recent_prices: list,
    portfolio_value: float = 750_000,
) -> dict:
    """Grade a swing trade setup. Caches by symbol across accounts."""
    if symbol in _grade_cache:
        logger.debug(f"    {symbol}: using cached grade")
        return _grade_cache[symbol]

    price_str = ", ".join(f"{p:.2f}" for p in recent_prices[-10:])
    prompt = f"""Grade this swing trade setup for a retirement portfolio. Respond ONLY with JSON.

Symbol: {symbol} | Signal: {signal} | Conviction: {conviction}/100
Price: ${price:.2f} | RSI: {rsi:.1f} | Vol ratio: {vol_ratio:.2f}x
Above SMA50: {above_sma50} | Above SMA200: {above_sma200} | EMA cross: {ema_cross}
Reason: {reason}
Recent 10 closes: [{price_str}]

{{
  "confidence": <0.0-1.0>,
  "action": "<{signal}>",
  "reasoning": "<1 sentence max>",
  "size_mult": <0.5|1.0|1.5|2.0>
}}"""

    result = llm_call(prompt, expect_json=True, timeout=20, tag=f"grade/{symbol}")

    if result and "confidence" in result:
        out = {
            "confidence": round(float(result.get("confidence", 0.6)), 3),
            "action":     str(result.get("action", signal)),
            "reasoning":  str(result.get("reasoning", "")),
            "size_mult":  float(result.get("size_mult", 1.0)),
        }
    else:
        out = {
            "confidence": round(conviction / 100.0, 2),
            "action":     signal,
            "reasoning":  "Fallback — AI unavailable.",
            "size_mult":  1.0,
        }

    _grade_cache[symbol] = out
    return out


def generate_all_narratives(signals: list[dict], portfolio_value: float) -> dict[str, str]:
    """
    Generate narratives for all actionable signals in batches of 3.
    Returns {symbol: narrative_string}.
    """
    if not signals:
        return {}

    BATCH_SIZE = 3
    all_narratives: dict[str, str] = {}

    for i in range(0, len(signals), BATCH_SIZE):
        batch = signals[i:i + BATCH_SIZE]
        lines = []
        for s in batch:
            sym    = s.get("symbol", "?")
            sig    = s.get("signal", "HOLD")
            cv     = s.get("conviction", 50)
            conf   = s.get("ai_confidence", 0.6)
            price  = s.get("price", 0)
            size   = s.get("suggested_usd", 0)
            reason = s.get("ai_reasoning", "")
            lines.append(
                f"{sym}: {sig} @ ${price:.2f} | cv={cv} | AI={conf:.0%} | "
                f"size=${size:,.0f} | {reason[:80]}"
            )

        symbols_list = ", ".join(f'"{s.get("symbol","?")}"' for s in batch)
        prompt = f"""Write a 1-sentence narrative for each swing trade signal.
Portfolio: ${portfolio_value:,.0f}. Plain prose. No bullets. No markdown.
Return ONLY valid JSON with exactly these keys: {{{symbols_list}: "narrative"}}

Signals:
{chr(10).join(lines)}"""

        result = llm_call(prompt, expect_json=True, timeout=20, tag="narratives")

        if isinstance(result, dict):
            for s in batch:
                sym = s.get("symbol", "?")
                if sym in result and result[sym]:
                    all_narratives[sym] = str(result[sym]).strip()
                else:
                    all_narratives[sym] = _fallback_narrative(s)
        else:
            for s in batch:
                all_narratives[s.get("symbol","?")] = _fallback_narrative(s)

    return all_narratives


def _fallback_narrative(s: dict) -> str:
    sym  = s.get("symbol", "?")
    sig  = s.get("signal", "HOLD")
    cv   = s.get("conviction", 50)
    conf = s.get("ai_confidence", 0.6)
    return f"{sym} {sig} — conviction {cv}/100, AI {conf:.0%}."


def detect_market_regime(spy_closes: list[float]) -> dict:
    """Classify market regime from recent SPY closes."""
    if len(spy_closes) < 5:
        return {"regime": "unknown", "bias": "neutral",
                "description": "Insufficient data", "signals": {}}

    closes     = spy_closes[-20:]
    recent     = closes[-5:]
    older      = closes[-10:-5]
    pct_change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0

    daily_changes = [
        abs((closes[i] - closes[i-1]) / closes[i-1] * 100)
        for i in range(1, len(closes))
    ]
    avg_daily_vol = sum(daily_changes) / len(daily_changes) if daily_changes else 0
    recent_avg = sum(recent) / len(recent)
    older_avg  = sum(older)  / len(older)
    trending   = abs(recent_avg - older_avg) / older_avg * 100 > 1.5 if older_avg else False

    if avg_daily_vol > 2.0:
        regime = "volatile";        bias = "bearish" if pct_change < 0 else "bullish"
        desc   = f"High volatility ({avg_daily_vol:.1f}%/day avg)"
    elif trending and pct_change > 2:
        regime = "trending";        bias = "bullish"
        desc   = f"Uptrend +{pct_change:.1f}% over {len(closes)} sessions"
    elif trending and pct_change < -2:
        regime = "trending";        bias = "bearish"
        desc   = f"Downtrend {pct_change:.1f}% over {len(closes)} sessions"
    elif avg_daily_vol < 0.7:
        regime = "low_volatility";  bias = "neutral"
        desc   = f"Low volatility ({avg_daily_vol:.1f}%/day avg) — choppy/ranging"
    else:
        regime = "ranging";         bias = "neutral"
        desc   = f"Ranging market, {pct_change:+.1f}% over {len(closes)} sessions"

    return {
        "regime":      regime,
        "bias":        bias,
        "description": desc,
        "signals": {
            "pct_change_20d": round(pct_change, 2),
            "avg_daily_vol":  round(avg_daily_vol, 2),
            "trending":       trending,
        },
    }


def generate_market_narrative(
    regime: dict,
    portfolio_summary: dict,
    top_movers: list,
    report_type: str = "EOD",
) -> str:
    """Generate 3–5 sentence market summary for EOD report."""
    regime_str  = regime.get("regime", "unknown")
    bias        = regime.get("bias", "neutral")
    description = regime.get("description", "")
    sigs        = regime.get("signals", {})

    mover_lines = "\n".join(
        f"  {m['symbol']:6} {m['signal']:12} cv={m['conviction']:3d}  [{m['account']}]"
        for m in top_movers[:5]
    ) or "  None"

    total_val = portfolio_summary.get("total_value", 0)
    pnl_today = portfolio_summary.get("total_pnl_today", 0)
    pnl_pct   = portfolio_summary.get("total_pnl_pct", 0)

    prompt = f"""Write a 3-5 sentence EOD market summary for a retirement portfolio investor.

Market regime: {regime_str} ({bias}) — {description}
SPY 20-day change: {sigs.get('pct_change_20d', 0):+.1f}%
Avg daily vol: {sigs.get('avg_daily_vol', 0):.1f}%
Portfolio: ${total_val:,.0f} | Today P&L: ${pnl_today:+,.0f} ({pnl_pct:+.1f}%)

Top signals today:
{mover_lines}

Plain prose. Professional tone. No bullets. Focus on what matters for tomorrow."""

    result = llm_call_text(prompt, timeout=20, tag="market_narrative")
    if result and len(result.strip()) > 30:
        return result.strip()

    return (
        f"Market is in a {regime_str} regime with {bias} bias. "
        f"SPY moved {sigs.get('pct_change_20d', 0):+.1f}% over the past 20 sessions "
        f"with average daily volatility of {sigs.get('avg_daily_vol', 0):.1f}%. "
        f"Portfolio closed at ${total_val:,.0f} ({pnl_pct:+.1f}% today). "
        f"Review signals below before tomorrow's open."
    )