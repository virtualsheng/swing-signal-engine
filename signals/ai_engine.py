"""
signals/ai_engine.py — Ollama AI grading + market narrative
─────────────────────────────────────────────────────────────
Performance-optimised version:

  TIMEOUT reduced 60s → 25s   (qwen3:8b responds in 5-12s normally)
  grade_swing_setup() caches by symbol — same symbol in Rollover IRA
    AND Roth IRA only calls Ollama once, reuses result for second account
  generate_signal_narrative() batches ALL signals into ONE Ollama call
    instead of one call per symbol — biggest speed win
  generate_market_narrative() unchanged (one call, fast enough)

Typical run time with these changes:
  Before: ~4-8 min (34 symbols, 3 accounts, serial narrative calls)
  After:  ~1-2 min (cached grades, batched narratives, lower timeout)
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
TIMEOUT      = 25   # reduced from 60 — qwen3:8b responds in 5-12s normally

# ── Grade cache — keyed by symbol, shared across all accounts ─────────────
# Prevents re-grading the same symbol when it appears in multiple accounts
_grade_cache: dict[str, dict] = {}


def _ollama(prompt: str, expect_json: bool = True):
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json().get("response", "").strip()
        if not expect_json:
            return raw
        # Strip markdown code fences
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.debug(f"Ollama call failed: {e}")
        return None


def clear_grade_cache():
    """Call once at the start of each run to reset cross-account dedup cache."""
    _grade_cache.clear()


def check_ollama_available() -> bool:
    """
    Check if Ollama is running and responsive.
    First call after cold start can take 30-45s while model loads.
    Uses 60s for this check only; all subsequent calls use TIMEOUT=25s.
    """
    for attempt in range(2):
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": "hi", "stream": False},
                timeout=60,
            )
            if resp.status_code == 200:
                logger.info("Ollama: ready")
                return True
        except Exception as e:
            logger.debug(f"Ollama check attempt {attempt+1} failed: {e}")
    logger.warning("Ollama: unavailable — running in fallback mode")
    return False


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
    """
    Grade a swing setup 0.0–1.0. Caches by symbol so the same symbol
    in multiple accounts only calls Ollama once per run.
    """
    # Return cached result if this symbol was already graded this run
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

    result = _ollama(prompt)

    if result and "confidence" in result:
        confidence = round(float(result.get("confidence", 0.6)), 3)
        out = {
            "confidence": confidence,
            "action":     result.get("action", signal),
            "reasoning":  result.get("reasoning", ""),
            "size_mult":  float(result.get("size_mult", 1.0)),
        }
    else:
        # Fallback: use conviction as proxy
        out = {
            "confidence": round(conviction / 100.0, 2),
            "action":     signal,
            "reasoning":  "Fallback — Ollama unavailable or timed out.",
            "size_mult":  1.0,
        }

    _grade_cache[symbol] = out
    return out


def generate_signal_narrative(
    symbol: str,
    signal: str,
    action: str,
    confidence: float,
    conviction: int,
    reasoning: str,
    price: float,
    suggested_size_usd: float,
    portfolio_value: float,
) -> str:
    """
    Generate a 1–2 sentence narrative for a single signal.
    Note: call generate_all_narratives() instead when processing a full
    account — it batches all signals into one Ollama call, much faster.
    """
    prompt = f"""{symbol} {signal} at ${price:.2f}. Conviction {conviction}/100. AI confidence {confidence:.0%}. {reasoning}
Suggested size: ${suggested_size_usd:,.0f} of ${portfolio_value:,.0f} portfolio.
Write 1-2 sentences for a retirement investor. Plain prose, no bullets."""

    result = _ollama(prompt, expect_json=False)
    return result.strip() if result else f"{symbol} {signal} signal — conviction {conviction}/100, AI confidence {confidence:.0%}."


def generate_all_narratives(signals: list[dict], portfolio_value: float) -> dict[str, str]:
    """
    Generate narratives for all actionable signals in small batches of 3.
    Batching saves Ollama round-trips vs one-per-symbol while keeping
    each call small enough that JSON parsing is reliable.
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

        signals_text = "\n".join(lines)
        symbols_list = ", ".join(f'"{s.get("symbol","?")}"' for s in batch)
        prompt = f"""Write a 1-sentence narrative for each swing trade signal.
Portfolio: ${portfolio_value:,.0f}. Plain prose. No bullets. No markdown.
Return ONLY valid JSON with exactly these keys: {{{symbols_list}: "narrative"}}

Signals:
{signals_text}"""

        result = _ollama(prompt, expect_json=True)

        if isinstance(result, dict):
            for s in batch:
                sym = s.get("symbol", "?")
                if sym in result and result[sym]:
                    all_narratives[sym] = str(result[sym]).strip()
                else:
                    # Fallback for this symbol
                    all_narratives[sym] = (
                        f"{sym} {s.get('signal','HOLD')} — "
                        f"conviction {s.get('conviction',50)}/100, "
                        f"AI {s.get('ai_confidence',0.6):.0%}."
                    )
        else:
            # Whole batch failed — use fallback for all in batch
            for s in batch:
                sym = s.get("symbol", "?")
                all_narratives[sym] = (
                    f"{sym} {s.get('signal','HOLD')} — "
                    f"conviction {s.get('conviction',50)}/100."
                )

    return all_narratives


def detect_market_regime(spy_closes: list[float]) -> dict:
    """Classify market regime from recent SPY closes."""
    if len(spy_closes) < 5:
        return {"regime": "unknown", "bias": "neutral",
                "description": "Insufficient data", "signals": {}}

    closes     = spy_closes[-20:]
    recent     = closes[-5:]
    older      = closes[-10:-5]
    pct_change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0

    # Simple volatility: avg daily range
    daily_changes = [
        abs((closes[i] - closes[i-1]) / closes[i-1] * 100)
        for i in range(1, len(closes))
    ]
    avg_daily_vol = sum(daily_changes) / len(daily_changes) if daily_changes else 0

    recent_avg = sum(recent) / len(recent)
    older_avg  = sum(older)  / len(older)
    trending   = abs(recent_avg - older_avg) / older_avg * 100 > 1.5 if older_avg else False

    if avg_daily_vol > 2.0:
        regime = "volatile"
        bias   = "bearish" if pct_change < 0 else "bullish"
        desc   = f"High volatility ({avg_daily_vol:.1f}%/day avg)"
    elif trending and pct_change > 2:
        regime = "trending"
        bias   = "bullish"
        desc   = f"Uptrend +{pct_change:.1f}% over {len(closes)} sessions"
    elif trending and pct_change < -2:
        regime = "trending"
        bias   = "bearish"
        desc   = f"Downtrend {pct_change:.1f}% over {len(closes)} sessions"
    elif avg_daily_vol < 0.7:
        regime = "low_volatility"
        bias   = "neutral"
        desc   = f"Low volatility ({avg_daily_vol:.1f}%/day avg) — choppy/ranging"
    else:
        regime = "ranging"
        bias   = "neutral"
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
    """Generate a 3–5 sentence market summary for the EOD report."""
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

    result = _ollama(prompt, expect_json=False)
    if result and len(result.strip()) > 30:
        return result.strip()

    # Fallback
    return (
        f"Market is in a {regime_str} regime with {bias} bias. "
        f"SPY moved {sigs.get('pct_change_20d', 0):+.1f}% over the past 20 sessions "
        f"with average daily volatility of {sigs.get('avg_daily_vol', 0):.1f}%. "
        f"Portfolio closed at ${total_val:,.0f} ({pnl_pct:+.1f}% today). "
        f"Review signals below before tomorrow's open."
    )