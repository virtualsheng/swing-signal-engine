"""
notifications/discord.py — Discord webhook for swing-signal-engine
────────────────────────────────────────────────────────────────────
Sends concise Discord alerts for the four key swing-signal events:
  1. Morning pre-market summary   (run_morning.py  ~7:30 AM)
  2. Confirmed BUY signal         (run_opening.py  ~9:50 AM)
  3. 3:50 PM SELL alert           (run_prelim.py   ~3:50 PM)
  4. EOD market close summary     (run_eod.py      ~4:15 PM)

Discord limits: 2000 chars per message, 25 fields per embed.
Uses embeds for structured data and plain text for narrative sections.

DISCORD_WEBHOOK_URL in .env:
  https://discord.com/api/webhooks/{id}/{token}
"""

import os
import requests

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


def _valid_url(url: str) -> bool:
    return bool(url) and "discord.com/api/webhooks/" in url


def send_discord_message(message: str, webhook_url: str = None) -> bool:
    """Send plain text (max 2000 chars). Used as fallback."""
    webhook = webhook_url or DISCORD_WEBHOOK_URL
    if not _valid_url(webhook):
        return False
    try:
        resp = requests.post(
            webhook, json={"content": message[:2000]}, timeout=10
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"[discord] error: {e}")
        return False


def _post_embed(embed: dict, content: str = "", webhook_url: str = None) -> bool:
    webhook = webhook_url or DISCORD_WEBHOOK_URL
    if not _valid_url(webhook):
        return False
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content[:2000]
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"[discord] embed error: {e}")
        return False


def _chunks(text: str, size: int = 1900):
    """Split text into ≤size-char chunks for multi-message sends."""
    for i in range(0, len(text), size):
        yield text[i:i + size]


# ── 1. Morning pre-market alert ────────────────────────────────────────────────

def send_morning_alert(
    today_str:       str,
    spy_pct:         float,
    qqq_pct:         float,
    vix:             float,
    futures_lines:   list[str],   # formatted futures snapshot lines
    active_signals:  list[dict],  # [{symbol, eod_signal, conviction, gap_pct, account}]
    top_headline:    str = "",
    narrative:       str = "",
) -> bool:
    """
    7:30 AM — Pre-market snapshot + today's watchlist.
    Compact enough to read on mobile without scrolling.
    """
    # Market header line
    spy_arrow  = "📈" if spy_pct >= 0 else "📉"
    qqq_arrow  = "📈" if qqq_pct >= 0 else "📉"
    vix_icon   = "😨" if vix > 25 else "😐" if vix > 18 else "😊"

    # Futures block (first 6 lines max to stay compact)
    fut_text = "\n".join(futures_lines[:6]) if futures_lines else "—"

    # Watchlist (BUY/SELL signals only, sorted by conviction)
    buy_sigs  = [s for s in active_signals if "BUY"  in s.get("eod_signal","")]
    sell_sigs = [s for s in active_signals if "SELL" in s.get("eod_signal","")]
    hold_sigs = [s for s in active_signals if "HOLD" in s.get("eod_signal","")]

    def sig_line(s):
        arrow = "🟢" if "BUY" in s["eod_signal"] else "🔴" if "SELL" in s["eod_signal"] else "⚪"
        pm    = s.get("gap_pct", 0)
        return (f"{arrow} **{s['symbol']}**  {s['eod_signal']} cv={s['conviction']}  "
                f"pre-mkt {pm:+.1f}%  [{s['account']}]")

    watchlist_lines = ([sig_line(s) for s in sorted(buy_sigs,  key=lambda x: -x["conviction"])] +
                       [sig_line(s) for s in sorted(sell_sigs, key=lambda x: -x["conviction"])] +
                       [sig_line(s) for s in sorted(hold_sigs, key=lambda x: -x["conviction"])[:3]])
    watchlist_text  = "\n".join(watchlist_lines) or "No active signals"

    fields = [
        {"name": "SPY pre-mkt",  "value": f"{spy_arrow} `{spy_pct:+.2f}%`",  "inline": True},
        {"name": "QQQ pre-mkt",  "value": f"{qqq_arrow} `{qqq_pct:+.2f}%`",  "inline": True},
        {"name": "VIX",          "value": f"{vix_icon} `{vix:.1f}`",           "inline": True},
        {"name": "Futures",      "value": f"```{fut_text}```",                  "inline": False},
        {"name": f"Watchlist ({len(active_signals)} signals)",
         "value": watchlist_text, "inline": False},
    ]
    if top_headline:
        fields.append({"name": "Top headline", "value": top_headline[:200], "inline": False})
    if narrative:
        fields.append({"name": "AI morning note", "value": narrative[:400], "inline": False})

    embed = {
        "title":       f"🌅  Morning Intel — {today_str}",
        "color":       0x378ADD,   # blue
        "fields":      fields[:25],
        "footer":      {"text": "swing-signal-engine · morning report"},
    }
    return _post_embed(embed)


# ── 2. Confirmed BUY alert ─────────────────────────────────────────────────────

def send_buy_alert(
    symbol:       str,
    account:      str,
    signal:       str,      # BUY or STRONG_BUY
    conviction:   int,
    price:        float,
    entry_price:  float,    # suggested entry (OR breakout level)
    stop_price:   float,
    target_price: float,
    suggested_usd: float,
    rsi:          float,
    vol_ratio:    float,
    above_sma50:  bool,
    above_sma200: bool,
    ai_reasoning: str = "",
    narrative:    str = "",
    today_str:    str = "",
) -> bool:
    """
    9:50 AM — Confirmed opening-range BUY signal with full trade levels.
    """
    risk     = entry_price - stop_price
    reward   = target_price - entry_price
    rr       = reward / risk if risk > 0 else 0
    rr_str   = f"1 : {rr:.1f}"

    sma_str  = ("✅ Above SMA50 & SMA200" if above_sma50 and above_sma200 else
                "⚠️ Above SMA50 only"     if above_sma50 else
                "⚠️ Above SMA200 only"    if above_sma200 else
                "❌ Below SMA50 & SMA200")

    fields = [
        {"name": "Signal",      "value": f"`{signal}`  cv=`{conviction}`",       "inline": True},
        {"name": "Account",     "value": f"`{account}`",                          "inline": True},
        {"name": "Price",       "value": f"`${price:.2f}`",                       "inline": True},
        {"name": "Entry ≤",     "value": f"`${entry_price:.2f}`",                 "inline": True},
        {"name": "Stop",        "value": f"`${stop_price:.2f}`",                  "inline": True},
        {"name": "Target",      "value": f"`${target_price:.2f}` (2:1 R)",        "inline": True},
        {"name": "R:R",         "value": f"`{rr_str}`",                           "inline": True},
        {"name": "Size",        "value": f"`${suggested_usd:,.0f}`",              "inline": True},
        {"name": "Vol ratio",   "value": f"`{vol_ratio:.2f}×`",                   "inline": True},
        {"name": "RSI",         "value": f"`{rsi:.0f}`",                          "inline": True},
        {"name": "Trend",       "value": sma_str,                                 "inline": False},
    ]
    if ai_reasoning:
        fields.append({"name": "AI note", "value": ai_reasoning[:300], "inline": False})
    if narrative:
        fields.append({"name": "Narrative", "value": narrative[:300], "inline": False})

    embed = {
        "title":       f"🟢  BUY  {symbol}  —  Execute now",
        "description": f"Opening range confirmed BUY signal for **{symbol}** [`{account}`]",
        "color":       0x1D9E75,   # green
        "fields":      fields[:25],
        "footer":      {"text": f"swing-signal-engine · {today_str}  opening signal"},
    }
    return _post_embed(embed)


# ── 3. 3:50 PM SELL alert ──────────────────────────────────────────────────────

def send_prelim_sell_alert(
    sell_signals: list[dict],   # [{symbol, account, signal, conviction, price,
                                #    chg_1d, rsi, vol_ratio, reason, narrative}]
    today_str:    str = "",
    spy_chg:      float = 0.0,
    qqq_chg:      float = 0.0,
    vix:          float = 0.0,
) -> bool:
    """
    3:50 PM — SELL/STRONG_SELL signals that emerged near close.
    Gives you 10 minutes to act before 4 PM.
    """
    if not sell_signals:
        return False

    lines = []
    for s in sorted(sell_signals, key=lambda x: -x["conviction"]):
        icon = "🔴🔴" if s["signal"] == "STRONG_SELL" else "🔴"
        lines.append(
            f"{icon} **{s['symbol']}**  {s['signal']} cv={s['conviction']}  "
            f"${s['price']:.2f} ({s.get('chg_1d',0):+.1f}% today)  "
            f"RSI={s.get('rsi',0):.0f}  [{s['account']}]"
        )
        if s.get("reason"):
            lines.append(f"   ↳ _{s['reason'][:80]}_")

    embed = {
        "title":       f"🔴  SELL ALERT  —  3:50 PM  {today_str}",
        "description": (f"**{len(sell_signals)} sell signal(s)** emerging near close — "
                        f"10 minutes to act.\n\n" + "\n".join(lines)),
        "color":       0xE24B4A,   # red
        "fields": [
            {"name": "SPY today",   "value": f"`{spy_chg:+.2f}%`", "inline": True},
            {"name": "QQQ today",   "value": f"`{qqq_chg:+.2f}%`", "inline": True},
            {"name": "VIX",         "value": f"`{vix:.1f}`",        "inline": True},
        ],
        "footer": {"text": "swing-signal-engine · preliminary sell alert"},
    }
    return _post_embed(embed)


# ── 4. EOD close summary ───────────────────────────────────────────────────────

def send_eod_summary(
    today_str:       str,
    spy_chg:         float,
    qqq_chg:         float,
    vix:             float,
    buy_signals:     list[dict],   # [{symbol, account, conviction, price, reason}]
    sell_signals:    list[dict],
    hold_signals:    list[dict],
    narrative:       str = "",
    total_value:     float = 0,
) -> bool:
    """
    4:15 PM — Full EOD summary: all actionable signals for tomorrow.
    """
    spy_arrow = "📈" if spy_chg >= 0 else "📉"
    qqq_arrow = "📈" if qqq_chg >= 0 else "📉"

    def sig_lines(sigs, icon):
        out = []
        for s in sorted(sigs, key=lambda x: -x["conviction"]):
            out.append(
                f"{icon} **{s['symbol']}**  cv={s['conviction']}  "
                f"${s['price']:.2f}  [{s['account']}]"
            )
            if s.get("reason"):
                out.append(f"   _{s['reason'][:70]}_")
        return "\n".join(out) if out else "None"

    fields = [
        {"name": "SPY",  "value": f"{spy_arrow} `{spy_chg:+.2f}%`", "inline": True},
        {"name": "QQQ",  "value": f"{qqq_arrow} `{qqq_chg:+.2f}%`", "inline": True},
        {"name": "VIX",  "value": f"`{vix:.1f}`",                    "inline": True},
        {"name": f"🟢 BUY ({len(buy_signals)})",
         "value": sig_lines(buy_signals, "🟢"),                       "inline": False},
    ]
    if sell_signals:
        fields.append({"name": f"🔴 SELL ({len(sell_signals)})",
                       "value": sig_lines(sell_signals, "🔴"),        "inline": False})
    if hold_signals:
        top_holds = sorted(hold_signals, key=lambda x: -x["conviction"])[:5]
        fields.append({"name": f"⚪ HOLD (top {len(top_holds)})",
                       "value": sig_lines(top_holds, "⚪"),            "inline": False})
    if narrative:
        fields.append({"name": "AI market commentary",
                       "value": narrative[:500],                       "inline": False})
    if total_value:
        fields.append({"name": "Portfolio",
                       "value": f"`${total_value:,.0f}`",              "inline": True})

    embed = {
        "title":       f"📊  EOD Signals — {today_str}  (for tomorrow)",
        "color":       0x7F77DD,   # purple
        "fields":      fields[:25],
        "footer":      {"text": "swing-signal-engine · end of day report"},
    }
    return _post_embed(embed)