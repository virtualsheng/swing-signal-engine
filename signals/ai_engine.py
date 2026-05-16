"""
ai_engine.py — Ollama AI grading + market narrative
─────────────────────────────────────────────────────
Three functions:
  grade_swing_setup()        — 0.0–1.0 confidence per symbol
  detect_market_regime()     — regime + bias from SPY closes
  generate_signal_narrative()— 2-sentence per-symbol explanation
  generate_market_narrative()— full 4–6 sentence daily market summary
                               using SPY/QQQ sector performance + your
                               portfolio's actual P&L context
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
TIMEOUT      = 60


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
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.debug(f"Ollama call failed: {e}")
        return None


def check_ollama_available() -> bool:
    """
    Check if Ollama is running and the model is responsive.
    Uses a 60-second timeout — the first call after a cold start
    can take 30-45 seconds while qwen3:8b loads into memory.
    Retries once on failure.
    """
    for attempt in range(2):
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": "hi", "stream": False},
                timeout=60,
            )
            if resp.status_code == 200:
                return True
        except Exception as e:
            logger.debug(f"Ollama check attempt {attempt+1} failed: {e}")
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
    price_str = ", ".join(f"{p:.2f}" for p in recent_prices[-10:])
    prompt = f"""You are a swing trading analyst for a retirement portfolio.

Symbol: {symbol}
Signal: {signal}
Technical conviction: {conviction}/100
Price: ${price:.2f}
RSI(14): {rsi:.1f}
Above SMA50: {above_sma50} | Above SMA200: {above_sma200}
Volume ratio: {vol_ratio:.2f}x | EMA cross: {ema_cross}
Reason: {reason}
Recent 10 closes: [{price_str}]
Portfolio: ${portfolio_value:,.0f}

Grade this swing setup for a long-term retirement account.
Respond ONLY with valid JSON, no markdown:
{{
  "confidence": <float 0.0-1.0>,
  "size_mult": <float 0.5-2.0>,
  "action": "<BUY|SELL|HOLD|STRONG_BUY|STRONG_SELL>",
  "reasoning": "<2 sentence explanation>"
}}"""

    result = _ollama(prompt, expect_json=True)
    if result and isinstance(result, dict) and "confidence" in result:
        result["confidence"] = float(max(0.0, min(1.0, result.get("confidence", 0.5))))
        result["size_mult"]  = float(max(0.5, min(2.0, result.get("size_mult",  1.0))))
        result["action"]     = result.get("action", signal)
        result["reasoning"]  = result.get("reasoning", "")
        return result

    confidence = conviction / 100.0
    return {
        "confidence": round(confidence, 2),
        "size_mult":  round(0.5 + confidence, 2),
        "action":     signal,
        "reasoning":  f"Fallback (Ollama unavailable). Conviction: {conviction}/100.",
    }


def detect_market_regime(spy_closes: list) -> dict:
    if len(spy_closes) < 20:
        return {"regime": "trending_up", "bias": "neutral", "description": "insufficient data"}

    price_str = ", ".join(f"{p:.2f}" for p in spy_closes[-20:])
    prompt = f"""Analyze last 20 SPY daily closes and classify market regime.
SPY (oldest→newest): [{price_str}]
Respond ONLY with valid JSON:
{{
  "regime": "<trending_up|trending_down|ranging|volatile>",
  "bias": "<bullish|bearish|neutral>",
  "description": "<1-2 sentence description>"
}}"""

    result = _ollama(prompt, expect_json=True)
    if result and isinstance(result, dict) and "regime" in result:
        return result

    recent = spy_closes[-5:]
    older  = spy_closes[-20:-15]
    pct    = (recent[-1] - older[0]) / older[0] * 100
    if pct > 3:
        return {"regime": "trending_up",   "bias": "bullish", "description": "SPY trending up"}
    elif pct < -3:
        return {"regime": "trending_down", "bias": "bearish", "description": "SPY trending down"}
    return {"regime": "ranging", "bias": "neutral", "description": "SPY ranging"}


def generate_market_narrative(
    regime: dict,
    portfolio_summary: dict,
    top_movers: list,
    report_type: str = "EOD",
) -> str:
    """
    Generate a 4–6 sentence market narrative for the top of the report.

    portfolio_summary: {
      "total_value":     float,
      "total_pnl_today": float,
      "total_pnl_pct":   float,
      "accounts": [
        {"name": str, "value": float, "pnl_today": float, "pnl_pct": float}
      ]
    }

    top_movers: [
      {"symbol": str, "chg_1d": float, "signal": str, "account": str}
    ]  — sorted by abs(chg_1d) descending, top 5
    """
    acct_lines = "\n".join(
        f"  {a['name']}: ${a['value']:,.0f}  today {a['pnl_today']:+,.0f} ({a['pnl_pct']:+.2f}%)"
        for a in portfolio_summary.get("accounts", [])
    )
    mover_lines = "\n".join(
        f"  {m['symbol']} {m['chg_1d']:+.2f}% ({m['signal']}) in {m['account']}"
        for m in top_movers[:5]
    )
    total_pnl   = portfolio_summary.get("total_pnl_today", 0)
    total_pct   = portfolio_summary.get("total_pnl_pct",   0)
    total_value = portfolio_summary.get("total_value",      0)

    session_label = "pre-market" if report_type == "PREMARKET" else "end of day"
    prompt = f"""You are a financial analyst writing a {session_label} portfolio summary for a retirement account holder.

Market regime: {regime.get('regime','').replace('_',' ')} — {regime.get('bias','')}
Regime description: {regime.get('description','')}

Portfolio performance today:
  Total value: ${total_value:,.0f}
  Today's P&L: ${total_pnl:+,.0f} ({total_pct:+.2f}%)
{acct_lines}

Biggest movers in the portfolio today:
{mover_lines}

Write a 4–6 sentence market narrative suitable for a daily report email.
Cover: overall market tone, what drove today's moves, portfolio-specific context,
and 1–2 forward-looking observations (what to watch tomorrow).
Write in a professional but conversational tone. No bullet points. Plain prose only.
Do not repeat numbers already shown in the report tables — reference them only if adding context."""

    result = _ollama(prompt, expect_json=False)
    if result and isinstance(result, str) and len(result) > 50:
        return result.strip()

    # Fallback
    direction = "gained" if total_pnl >= 0 else "lost"
    return (
        f"Markets ended the session in a {regime.get('regime','').replace('_',' ')} regime "
        f"with a {regime.get('bias','neutral')} bias. "
        f"Your portfolio {direction} ${abs(total_pnl):,.0f} ({total_pct:+.2f}%) today. "
        f"{regime.get('description','')} "
        f"Review the signals below and consider any BUY/SELL recommendations before tomorrow's open."
    )


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
    prompt = f"""Write a 2-sentence swing trade recommendation for a retirement account report.
Symbol: {symbol} | Signal: {action} | AI confidence: {confidence:.0%}
Conviction: {conviction}/100 | Price: ${price:.2f}
Suggested position: ${suggested_size_usd:,.0f} ({suggested_size_usd/portfolio_value*100:.1f}% of portfolio)
Reasoning: {reasoning}
First sentence: what the technicals show. Second sentence: what action to take and why.
Professional, concise. No JSON."""

    result = _ollama(prompt, expect_json=False)
    if result and isinstance(result, str) and len(result) > 20:
        return result.strip()

    return (
        f"{symbol} showing {signal.lower()} signal with {conviction}/100 conviction "
        f"(AI: {confidence:.0%}). "
        f"{'Consider entering' if signal == 'BUY' else 'Consider reducing' if signal == 'SELL' else 'Hold'} "
        f"at ${price:.2f} — {reasoning}."
    )