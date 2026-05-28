"""
run_prelim.py — Swing Signal Engine
─────────────────────────────────────────────────────────────────────
3:50 PM PRELIMINARY report — runs 10 minutes before market close.
Gives you a window to act before 4 PM if a signal is strong enough
to trade today rather than waiting until tomorrow.

Schedule (Windows Task Scheduler):
  3:50 PM ET  →  python run_prelim.py

What it does:
  - Runs the same technical signal engine as run_eod.py
  - Uses near-close prices (10 min before official close)
  - Shows LIVE market prices (DOW, S&P 500, etc.) — NOT futures
  - Sends rich HTML email matching the Opening Report style
  - Does NOT overwrite signal_log.json — that's run_eod.py's job
  - Does NOT run AI grading (too slow for 10-min window)
  - Flags anything that changed since yesterday's EOD signal

Subject: SWING SIGNAL: PRELIM 3:50 PM — ACT NOW: SMH, NVDA | WATCH: QQQ
"""

import json
import logging
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

os.makedirs("logs",  exist_ok=True)
os.makedirs("cache", exist_ok=True)

_log_file = f"logs/prelim_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signals.data_fetcher       import fetch_batch
from signals.signal_engine      import get_technical_signal
from signals.portfolio          import load_portfolio, get_tradeable_accounts
from signals.market_futures     import (
    get_futures_snapshot, get_top_headline,
    format_futures_text, format_futures_html,
)
from notifications.notifier     import deliver_report
from run_eod                    import load_symbols_for_account

SIGNAL_LOG_FILE = "cache/signal_log.json"
MIN_CONVICTION  = int(os.getenv("SWING_MIN_CONVICTION", "65"))
TODAY_STR       = datetime.today().strftime("%Y-%m-%d")


# ── helpers ────────────────────────────────────────────────────────────────

def load_signal_log() -> dict:
    try:
        with open(SIGNAL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def changed_since_eod(symbol: str, new_action: str, signal_log: dict) -> str:
    """Return a change tag if signal differs from last night's EOD."""
    for k, v in signal_log.items():
        if k.endswith(f":{symbol}"):
            prev = v.get("signal", "HOLD")
            if prev != new_action:
                return f"← was {prev}"
            return ""
    return "← NEW"


def _sig_color(action: str) -> str:
    return {
        "BUY":        "#1D9E75", "STRONG_BUY":  "#1D9E75",
        "SELL":       "#E24B4A", "STRONG_SELL": "#E24B4A",
        "HOLD":       "#BA7517",
    }.get(action, "#888780")


def _sig_emoji(action: str) -> str:
    return {
        "BUY": "🟢", "STRONG_BUY": "🟢🟢",
        "SELL": "🔴", "STRONG_SELL": "🔴🔴",
        "HOLD": "⚪",
    }.get(action, "⚪")


def _sig_label(action: str) -> str:
    return {
        "BUY": "BUY", "STRONG_BUY": "STRONG BUY",
        "SELL": "SELL", "STRONG_SELL": "STRONG SELL",
        "HOLD": "HOLD",
    }.get(action, action)


# ── HTML builder ────────────────────────────────────────────────────────────

def build_prelim_html(
    act_now:      list,
    watch:        list,
    holds:        list,
    market_snap:  list,
    top_headline: dict,
    now_str:      str,
    total_value:  float,
) -> str:

    # ── Market bar — live close prices (mode="close") ──────────────────────
    market_bar_html = ""
    if market_snap:
        market_bar_html = format_futures_html(market_snap, top_headline or {})

    em_html = ""

    # ── Signal table builder ───────────────────────────────────────────────
    def signal_rows(items: list) -> str:
        rows = ""
        for e in items:
            sc      = _sig_color(e["action"])
            emoji   = _sig_emoji(e["action"])
            label   = _sig_label(e["action"])
            change  = e.get("change", "")
            change_html = (
                f'<span style="color:#BA7517;font-size:11px;margin-left:6px">{change}</span>'
                if change else ""
            )
            rsi_c = (
                "#E24B4A" if e["rsi"] > 70 else
                "#1D9E75" if e["rsi"] < 30 else "#888"
            )
            rows += f"""
            <tr style="border-top:0.5px solid #E8E6DF">
              <td style="padding:8px 10px;font-weight:500">{e['symbol']}</td>
              <td style="padding:8px 10px;color:{sc};font-weight:500">
                {emoji} {label}
              </td>
              <td style="padding:8px 10px;text-align:center">
                <span style="font-size:12px;font-weight:500">{e['conviction']}</span>
                <span style="color:#888;font-size:10px">/100</span>
              </td>
              <td style="padding:8px 10px;color:{rsi_c};font-size:12px">{e['rsi']:.1f}</td>
              <td style="padding:8px 10px;font-size:12px;color:#5F5E5A">{e['account']}</td>
              <td style="padding:8px 10px;font-size:11px">{change_html}</td>
            </tr>"""
        return rows

    th = 'style="padding:6px 10px;font-weight:400;color:#5F5E5A;font-size:12px;background:#F7F5EE;text-align:left"'
    thead = (f'<tr><th {th}>Symbol</th><th {th}>Signal</th>'
             f'<th {th}>CV</th><th {th}>RSI</th>'
             f'<th {th}>Account</th><th {th}>vs EOD</th></tr>')

    # ── ACT NOW section ────────────────────────────────────────────────────
    act_section = ""
    if act_now:
        act_section = f"""
        <div style="margin-bottom:16px;border:2px solid #1D9E75;border-radius:8px;overflow:hidden">
          <div style="background:#E1F5EE;padding:10px 14px;border-bottom:1px solid #9FE1CB">
            <span style="font-weight:500;color:#0F6E56;font-size:15px">
              ⚡ ACT NOW — {len(act_now)} signal{'s' if len(act_now)!=1 else ''}
            </span>
            <span style="color:#0F6E56;font-size:12px;margin-left:8px">
              conviction ≥ {MIN_CONVICTION} | ~10 min to market close
            </span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead>
            <tbody>{signal_rows(act_now)}</tbody>
          </table>
        </div>"""
    else:
        act_section = """
        <div style="margin-bottom:16px;padding:12px 14px;border:0.5px solid #D3D1C7;
                    border-radius:8px;color:#888;font-size:13px">
          No high-conviction signals at 3:50 PM — check the 4:15 PM EOD report.
        </div>"""

    # ── WATCH section ──────────────────────────────────────────────────────
    watch_section = ""
    if watch:
        watch_section = f"""
        <div style="margin-bottom:16px;border:1px solid #FAC775;border-radius:8px;overflow:hidden">
          <div style="background:#FAEEDA;padding:8px 14px;border-bottom:0.5px solid #FAC775">
            <span style="font-weight:500;color:#633806">
              👁 WATCH — {len(watch)} below min conviction
            </span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead>
            <tbody>{signal_rows(watch)}</tbody>
          </table>
        </div>"""

    # ── HOLDS compact row ──────────────────────────────────────────────────
    holds_html = ""
    if holds:
        hold_tags = "".join(
            f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
            f'background:#F1EFE8;border-radius:4px;font-size:12px;color:#5F5E5A">'
            f'{e["symbol"]} <span style="color:#888">({e["conviction"]})</span></span>'
            for e in holds[:24]
        )
        holds_html = f"""
        <div style="margin-bottom:16px;padding:10px 14px;border:0.5px solid #D3D1C7;
                    border-radius:8px">
          <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px">
            HOLDS ({len(holds)})
          </div>
          <div style="line-height:2">{hold_tags}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       max-width:860px;margin:0 auto;padding:20px;color:#2C2C2A;background:#fff;font-size:14px}}
</style>
</head><body>

<div style="border-bottom:2px solid #BA7517;padding-bottom:12px;margin-bottom:16px">
  <h1 style="margin:0;font-size:20px;font-weight:500">
    Preliminary Signals — 3:50 PM
  </h1>
  <p style="margin:4px 0 0;font-size:13px;color:#5F5E5A">
    {TODAY_STR} {now_str} &nbsp;|&nbsp; Portfolio: ${total_value:,.0f}
  </p>
  <p style="margin:4px 0 0;font-size:12px;color:#888">
    Near-close prices &nbsp;·&nbsp; No AI grading &nbsp;·&nbsp;
    ~10 min to act &nbsp;·&nbsp; Official EOD at 4:15 PM
  </p>
</div>

{market_bar_html}
{em_html}
{act_section}
{watch_section}
{holds_html}

<div style="border-top:0.5px solid #D3D1C7;padding-top:10px;margin-top:4px;
            font-size:11px;color:#888">
  ⚠️ Near-close prices — may differ slightly from official 4 PM close.
  Official EOD signals arrive at ~4:15 PM via run_eod.py.
  Only act on ACT NOW signals with conviction ≥ {MIN_CONVICTION}.
</div>
</body></html>"""


# ── Text fallback ───────────────────────────────────────────────────────────

def build_prelim_text(
    act_now:      list,
    watch:        list,
    holds:        list,
    market_snap:  list,
    top_headline: dict,
    now_str:      str,
) -> str:
    lines = [
        "=" * 62,
        f"  PRELIMINARY SIGNALS — {now_str} ET ({TODAY_STR})",
        "  Near-close  |  No AI grading  |  ~10 min to act",
        "=" * 62, "",
    ]

    # Live market snapshot
    if market_snap:
        lines += ["MARKET (live close prices):"]
        lines += [format_futures_text(market_snap), ""]
    if top_headline.get("title"):
        lines += [f"  📰 {top_headline['title']}", ""]


    # ACT NOW
    if act_now:
        lines.append(f"{'─'*62}")
        lines.append(f"  ⚡ ACT NOW ({len(act_now)}) — conviction ≥ {MIN_CONVICTION}:")
        for e in act_now:
            change = f"  {e['change']}" if e.get("change") else ""
            lines.append(
                f"  {_sig_emoji(e['action'])} {e['symbol']:6} "
                f"{_sig_label(e['action']):<12} cv={e['conviction']:3d} "
                f"RSI={e['rsi']:5.1f}  [{e['account']}]{change}"
            )
        lines.append("")

    # WATCH
    if watch:
        lines.append(f"{'─'*62}")
        lines.append(f"  👁 WATCH ({len(watch)}):")
        for e in watch:
            change = f"  {e['change']}" if e.get("change") else ""
            lines.append(
                f"  {_sig_emoji(e['action'])} {e['symbol']:6} "
                f"{_sig_label(e['action']):<12} cv={e['conviction']:3d}  "
                f"[{e['account']}]{change}"
            )
        lines.append("")

    # Holds
    lines += [
        f"{'─'*62}",
        "  HOLDS: " + "  ".join(
            f"{e['symbol']}({e['conviction']})" for e in holds[:20]
        ),
        "",
        "─" * 62,
        f"  Official EOD signals at ~4:15 PM via run_eod.py.",
        "─" * 62,
    ]
    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────────────

def run():
    logger.info("=== PRELIM Signal Run — 3:50 PM ===")

    portfolio   = load_portfolio()
    tradeable   = get_tradeable_accounts(portfolio)
    signal_log  = load_signal_log()
    total_value = sum(
        v.get("account_value", 0)
        for v in portfolio.get("accounts", {}).values()
    )

    # Collect all unique symbols across tradeable accounts
    all_symbols: dict[str, str] = {}  # symbol → account_name
    for acct_name in tradeable:
        for sym in load_symbols_for_account(acct_name):
            if sym not in all_symbols:
                all_symbols[sym] = acct_name

    if not all_symbols:
        logger.warning("No symbols found — check symbols.txt")
        return

    logger.info(f"Running prelim signals for {len(all_symbols)} symbols...")

    # ── Live market snapshot — mode="close" gives actual prices, not futures ──
    # At 3:50 PM the market is still open so regularMarketPrice IS the live
    # intraday price, not a futures contract. Labels show "DOW/S&P 500/NASDAQ".
    logger.info("Fetching live market snapshot...")
    market_snap  = get_futures_snapshot(force=True, mode="close")
    top_headline = get_top_headline()
    fetched = sum(1 for f in market_snap if f.get("price") is not None)
    logger.info(f"Market snapshot: {fetched}/8 | headline: {'yes' if top_headline.get('title') else 'no'}")

    # ── Near-close bars ────────────────────────────────────────────────────
    bars = fetch_batch(list(all_symbols.keys()))

    # ── Technical signals ──────────────────────────────────────────────────
    act_now, watch, holds = [], [], []

    for symbol, acct_name in sorted(all_symbols.items()):
        if symbol not in bars or bars[symbol] is None or bars[symbol].empty:
            logger.warning(f"  {symbol}: no data")
            continue
        try:
            result     = get_technical_signal(symbol, bars[symbol])
            action     = result.get("action", "HOLD")
            conviction = int(min(
                result.get("bull_score", 0) + abs(result.get("bear_score", 0)),
                99
            ))
            change = changed_since_eod(symbol, action, signal_log)

            entry = {
                "symbol":     symbol,
                "account":    acct_name,
                "action":     action,
                "conviction": conviction,
                "rsi":        result.get("rsi", 50),
                "change":     change,
            }

            if action in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
                if conviction >= MIN_CONVICTION:
                    act_now.append(entry)
                else:
                    watch.append(entry)
            else:
                holds.append(entry)

        except Exception as e:
            logger.error(f"  {symbol}: {e}")

    act_now.sort(key=lambda x: x["conviction"], reverse=True)
    watch.sort(key=lambda x: x["conviction"], reverse=True)
    logger.info(f"ACT NOW: {len(act_now)} | WATCH: {len(watch)} | HOLD: {len(holds)}")


    # ── Build subject ──────────────────────────────────────────────────────
    now_str   = datetime.now().strftime("%I:%M %p")
    act_syms  = [e["symbol"] for e in act_now]
    watch_syms = [e["symbol"] for e in watch]

    parts = []
    if act_syms:
        parts.append(f"ACT NOW: {', '.join(act_syms[:4])}")
    if watch_syms:
        parts.append(f"WATCH: {', '.join(watch_syms[:4])}")
    if not parts:
        parts.append("No actionable signals")

    subject = f"SWING SIGNAL: PRELIM {now_str} — {' | '.join(parts)}"

    # ── Build and deliver ──────────────────────────────────────────────────
    html_body = build_prelim_html(
        act_now, watch, holds,
        market_snap, top_headline, now_str, total_value,
    )
    text_body = build_prelim_text(
        act_now, watch, holds,
        market_snap, top_headline, now_str,
    )

    logger.info(f"Sending: {subject}")
    deliver_report(subject, html_body, text_body)

    # Rich Discord SELL alert — only if there are SELL signals at 3:50 PM
    _sell_at_prelim = [e for e in act_now if "SELL" in e.get("signal","")]
    if _sell_at_prelim:
        try:
            from notifications.discord import send_prelim_sell_alert
            _snap  = market_snap or []
            _spy_c = next((f.get("chg_val",0) for f in _snap if f.get("label","")=="S&P 500"), 0)
            _qqq_c = next((f.get("chg_val",0) for f in _snap if "NAS" in f.get("label","")), 0)
            _vix_v = next((f.get("price",0)   for f in _snap if "10-YR" in f.get("label","") or
                           f.get("label","").startswith("VIX")), 0)
            send_prelim_sell_alert(
                sell_signals = [
                    {"symbol":     e["symbol"],
                     "account":    e.get("account",""),
                     "signal":     e.get("signal","SELL"),
                     "conviction": e.get("conviction", 0),
                     "price":      e.get("price", 0),
                     "chg_1d":     e.get("chg_1d", 0),
                     "rsi":        e.get("rsi", 50),
                     "reason":     e.get("reason",""),
                    } for e in _sell_at_prelim
                ],
                today_str = TODAY_STR,
                spy_chg   = float(_spy_c),
                qqq_chg   = float(_qqq_c),
                vix       = float(_vix_v),
            )
        except Exception as _de:
            logger.debug(f"Discord SELL alert skipped: {_de}")

    logger.info("Done.")


if __name__ == "__main__":
    run()