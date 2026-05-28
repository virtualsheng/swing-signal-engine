"""
run_opening.py — Opening Range Confirmation (9:50 AM ET)
──────────────────────────────────────────────────────────
THIS IS YOUR TRADE LIST. Run after the first 15 minutes of price
action (9:30–9:45 AM) have printed. Reads last night's EOD signals
and confirms or invalidates each one based on real opening data.

For each active signal, tells you:
  EXECUTE NOW  — signal confirmed by opening action. Entry price,
                 stop loss, and target price included.
  WAIT         — signal present but opening not yet confirming.
                 Specific level to watch for confirmation.
  STAND DOWN   — opening action invalidates the signal.
                 Skip this trade today.

Schedule: 9:50 AM ET daily (Mon–Fri)
  python run_opening.py

Quick Fidelity execution workflow:
  1. Read this report (~2 min)
  2. For each EXECUTE NOW: place limit order in Fidelity near entry price
  3. Set a mental stop at the stop price
  4. Check again at noon — any WAIT signals may have confirmed by then
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

_log_file = f"logs/opening_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

from signals.opening_range  import confirm_signal, get_avg_volume
from signals.portfolio      import (
    load_portfolio, get_tradeable_accounts, get_position_in,
)
from signals.ai_engine      import check_ollama_available
from signals.market_futures import (
    get_futures_snapshot, get_top_headline,
    format_futures_html, format_futures_text,
)
from notifications.notifier import deliver_report

SIGNAL_LOG_FILE = "cache/signal_log.json"


def load_signal_log() -> dict:
    try:
        with open(SIGNAL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _ollama_opening_narrative(ollama_ok: bool, execute_list: list, wait_list: list, stand_down: list) -> str:
    if not ollama_ok or (not execute_list and not wait_list):
        if not execute_list:
            return "No confirmed trades this morning. All signals either standing down or waiting for confirmation."
        return (
            f"{len(execute_list)} trade(s) confirmed by opening action. "
            f"Execute in Fidelity near the entry prices shown. "
            f"Respect the stop levels — they are based on the opening range low."
        )

    import requests
    exec_lines = "\n".join(
        f"  {t['symbol']:6} {t['eod_signal']:12} entry ${t['entry_price']:.2f} "
        f"stop ${t['stop_price']:.2f} target ${t['target_price']:.2f} [{t['account']}]"
        for t in execute_list
    ) or "  None"
    wait_lines = "\n".join(
        f"  {t['symbol']:6} watching for {t.get('watch_level','confirmation')}"
        for t in wait_list[:3]
    ) or "  None"
    sd_lines = "\n".join(
        f"  {t['symbol']:6} {t.get('reason','opening action contradicts signal')}"
        for t in stand_down[:3]
    ) or "  None"

    prompt = f"""You are writing a 3–4 sentence opening trade briefing for a retirement account investor.

EXECUTE NOW (confirmed by opening action):
{exec_lines}

WAIT (not yet confirmed):
{wait_lines}

STAND DOWN (signal invalidated):
{sd_lines}

Write practical execution guidance. Cover:
1. Which trades to execute and how urgently
2. Any caution notes on stops or sizing
3. What to watch for the WAIT signals

Keep it short, direct, actionable. No bullet points. No financial advice disclaimer."""

    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen3:8b", "prompt": prompt, "stream": False},
            timeout=45,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            if len(text) > 30:
                return text
    except Exception:
        pass

    return (
        f"{len(execute_list)} trade(s) confirmed for execution. "
        f"Place limit orders near the entry prices shown and respect stop levels. "
        f"{len(wait_list)} signal(s) still waiting for confirmation."
    )


def build_opening_html(
    today_str: str,
    execute_list: list,
    wait_list: list,
    stand_down_list: list,
    opening_narrative: str,
    total_value: float,
    futures_snap: list = None,
    top_headline: dict = None,
) -> str:
    now = datetime.now().strftime("%H:%M ET")

    def action_color(action):
        return {"EXECUTE NOW":"#1D9E75","WAIT":"#BA7517","STAND DOWN":"#E24B4A","HOLD":"#888"}.get(action,"#888")

    def sig_color(s):
        return {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75","SELL":"#E24B4A","STRONG_SELL":"#E24B4A"}.get(s,"#888")

    def trade_rows(items, show_levels=True):
        rows = ""
        for t in items:
            sig_c = sig_color(t["eod_signal"])
            act_c = action_color(t["action"])
            held_html = ""
            if t.get("shares_held") and t.get("avg_cost"):
                held_html = f'<br><span style="font-size:11px;color:#888">held {t["shares_held"]:.0f} sh @ ${t["avg_cost"]:.2f}</span>'
            levels_html = ""
            if show_levels and t["action"] == "EXECUTE NOW":
                rr = ""
                if t.get("entry_price") and t.get("stop_price") and t.get("target_price"):
                    risk   = t["entry_price"] - t["stop_price"]
                    reward = t["target_price"] - t["entry_price"]
                    rr     = f"R/R {reward/risk:.1f}:1" if risk > 0 else ""
                levels_html = f"""
                <br>
                <span style="font-size:12px">
                  Entry <span style="color:#1D9E75;font-weight:500">${t.get('entry_price',0):.2f}</span>
                  &nbsp;Stop <span style="color:#E24B4A;font-weight:500">${t.get('stop_price',0):.2f}</span>
                  &nbsp;Target <span style="color:#1D9E75">${t.get('target_price',0):.2f}</span>
                  &nbsp;<span style="color:#888">{rr}</span>
                </span>"""
            elif show_levels and t["action"] == "WAIT":
                levels_html = f'<br><span style="font-size:11px;color:#BA7517">{t.get("reasoning","")[:80]}</span>'

            or_html = ""
            if t.get("opening_range_high") and t.get("opening_range_low"):
                or_html = (f'<span style="color:#888;font-size:11px">'
                           f'OR: ${t["opening_range_low"]:.2f}–${t["opening_range_high"]:.2f}'
                           f' | candle: {t.get("candle_direction","?")} '
                           f'| vol: {t.get("open_volume_ratio",0):.1f}x</span>')

            size_html = ""
            if t.get("suggested_usd") and t["action"] == "EXECUTE NOW":
                sz  = t["suggested_usd"]
                sp  = sz / t.get("acct_value", sz) * 100 if t.get("acct_value") else 0
                pos_val = t.get("entry_price",0) * (sz / t.get("entry_price",1)) if t.get("entry_price") else 0
                size_html = f'<span style="color:#1D9E75;font-weight:500">${sz:,.0f}</span><span style="color:#888;font-size:11px"> ({sp:.1f}%)</span>'

            rows += f"""
            <tr style="border-top:0.5px solid #E8E6DF">
              <td style="padding:10px 8px;font-weight:500">
                {t['symbol']}{held_html}
              </td>
              <td style="padding:10px 8px;color:{sig_c};font-weight:500">{t['eod_signal']}</td>
              <td style="padding:10px 8px">
                <span style="color:{act_c};font-weight:500">{t['action']}</span>
                {levels_html}
              </td>
              <td style="padding:10px 8px">{or_html}</td>
              <td style="padding:10px 8px">{size_html}</td>
              <td style="padding:10px 8px;font-size:12px;color:#5F5E5A">{t.get('account','')}</td>
            </tr>"""
        return rows

    th = 'style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px;background:#F7F5EE"'
    thead = f'<tr><th {th}>Symbol</th><th {th}>Signal</th><th {th}>Action / Levels</th><th {th}>Opening range</th><th {th}>Size</th><th {th}>Account</th></tr>'

    execute_section = ""
    if execute_list:
        execute_section = f"""
        <div style="margin-bottom:20px;border:2px solid #1D9E75;border-radius:8px;overflow:hidden">
          <div style="background:#E1F5EE;padding:10px 14px;border-bottom:1px solid #9FE1CB">
            <span style="font-weight:500;color:#0F6E56;font-size:15px">
              ✅ EXECUTE NOW — {len(execute_list)} confirmed trade{'s' if len(execute_list)!=1 else ''}
            </span>
            <span style="color:#0F6E56;font-size:12px;margin-left:8px">Place orders in Fidelity</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead><tbody>{trade_rows(execute_list)}</tbody>
          </table>
        </div>"""
    else:
        execute_section = """
        <div style="margin-bottom:20px;padding:14px;border:0.5px solid #D3D1C7;border-radius:8px;color:#888;font-size:13px">
          No confirmed trades this morning.
        </div>"""

    wait_section = ""
    if wait_list:
        wait_section = f"""
        <div style="margin-bottom:20px;border:1px solid #FAC775;border-radius:8px;overflow:hidden">
          <div style="background:#FAEEDA;padding:10px 14px;border-bottom:0.5px solid #FAC775">
            <span style="font-weight:500;color:#633806">⏳ WAIT — {len(wait_list)} not yet confirmed</span>
            <span style="color:#854F0B;font-size:12px;margin-left:8px">Check again at 10:30 AM if still watching</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead><tbody>{trade_rows(wait_list, show_levels=True)}</tbody>
          </table>
        </div>"""

    sd_section = ""
    if stand_down_list:
        sd_rows = ""
        for t in stand_down_list:
            sd_rows += f"""
            <tr style="border-top:0.5px solid #E8E6DF">
              <td style="padding:7px 8px;color:#888;font-weight:500">{t['symbol']}</td>
              <td style="padding:7px 8px;color:#888">{t['eod_signal']}</td>
              <td colspan="4" style="padding:7px 8px;color:#E24B4A;font-size:12px">⛔ {t.get('reasoning','Signal invalidated by opening action')}</td>
            </tr>"""
        sd_section = f"""
        <div style="margin-bottom:20px;border:0.5px solid #F09595;border-radius:8px;overflow:hidden">
          <div style="background:#FCEBEB;padding:10px 14px;border-bottom:0.5px solid #F09595">
            <span style="font-weight:500;color:#A32D2D">⛔ STAND DOWN — {len(stand_down_list)} invalidated</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead><tbody>{sd_rows}</tbody>
          </table>
        </div>"""

    # Market bar: mode="close" = actual live prices (not futures) at 9:50 AM
    market_bar_html = ""
    if futures_snap:
        market_bar_html = format_futures_html(futures_snap, top_headline or {})

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:860px;margin:0 auto;padding:20px;color:#2C2C2A;background:#fff;font-size:14px}}</style>
</head><body>
<div style="border-bottom:2px solid #1D9E75;padding-bottom:12px;margin-bottom:20px">
  <h1 style="margin:0;font-size:20px;font-weight:500">Opening Range Confirmation — Trade List</h1>
  <p style="margin:4px 0 0;font-size:13px;color:#5F5E5A">{today_str} {now} &nbsp;|&nbsp; Portfolio: ${total_value:,.0f}</p>
  <p style="margin:4px 0 0;font-size:12px;color:#888">Based on first 15 min of price action (9:30–9:45 AM opening range)</p>
</div>
{market_bar_html}
<div style="margin-bottom:20px;padding:14px 16px;border-left:3px solid #1D9E75;background:#FAFAF8;border-radius:0 6px 6px 0">
  <p style="margin:0;font-size:13px;line-height:1.7;color:#2C2C2A">{opening_narrative}</p>
</div>
{execute_section}
{wait_section}
{sd_section}
<div style="border-top:0.5px solid #D3D1C7;padding-top:12px;margin-top:8px;font-size:11px;color:#888">
  Entry prices are suggestions based on opening range analysis. Use limit orders. Stops are based on opening range lows.
  Always verify in Fidelity before executing. Not financial advice.
</div>
</body></html>"""


def build_opening_text(execute_list, wait_list, stand_down_list, opening_narrative, today_str, total_value, futures_snap=None) -> str:
    now = datetime.now().strftime("%H:%M ET")
    lines = [
        "═" * 62,
        f"  OPENING REPORT — TRADE LIST — {today_str} {now}",
        f"  Portfolio: ${total_value:,.0f}",
        "═" * 62, "",
    ]
    if futures_snap:
        lines += ["MARKET (live prices):", format_futures_text(futures_snap), ""]
    lines += [f"  {opening_narrative}", ""]
    if execute_list:
        lines.append(f"  ✅ EXECUTE NOW ({len(execute_list)} trade{'s' if len(execute_list)!=1 else ''}):")
        for t in execute_list:
            lines.append(
                f"  {'🟢' if 'BUY' in t['eod_signal'] else '🔴'} "
                f"{t['symbol']:6} {t['eod_signal']:12} "
                f"entry ${t.get('entry_price',0):.2f} "
                f"stop ${t.get('stop_price',0):.2f} "
                f"target ${t.get('target_price',0):.2f} "
                f"→ ${t.get('suggested_usd',0):,.0f}  [{t.get('account','')}]"
            )
            lines.append(f"     {t.get('reasoning','')[:100]}")
        lines.append("")
    if wait_list:
        lines.append(f"  ⏳ WAIT ({len(wait_list)} — check at 10:30 AM):")
        for t in wait_list:
            lines.append(f"     {t['symbol']:6} {t['eod_signal']:12} — {t.get('reasoning','')[:80]}")
        lines.append("")
    if stand_down_list:
        lines.append(f"  ⛔ STAND DOWN ({len(stand_down_list)} invalidated):")
        for t in stand_down_list:
            lines.append(f"     {t['symbol']:6} — {t.get('reasoning','')[:80]}")
        lines.append("")
    lines += ["═" * 62]
    return "\n".join(lines)


def run():
    logger.info("=== Opening Range Confirmation — 9:50 AM ===")

    portfolio   = load_portfolio()
    tradeable   = get_tradeable_accounts(portfolio)
    signal_log  = load_signal_log()
    today_str   = datetime.today().strftime("%Y-%m-%d")
    total_value = sum(
        v.get("account_value", 0)
        for v in portfolio.get("accounts", {}).values()
    )
    ollama_ok = check_ollama_available()

    execute_list    = []
    wait_list       = []
    stand_down_list = []

    for acct_name, acct_cfg in tradeable.items():
        acct_value = acct_cfg.get("account_value", 0)
        asset_class = acct_cfg.get("asset_class", "etf")

        for sym in acct_cfg.get("positions", {}).keys():
            key = f"{acct_name}:{sym}"
            sig = signal_log.get(key, {})

            # Only process active (non-blocked) BUY/SELL signals
            if sig.get("signal") not in ("BUY","SELL","STRONG_BUY","STRONG_SELL"):
                continue
            if sig.get("blocked_by"):
                continue

            logger.info(f"  Confirming {sym} ({sig['signal']}) in {acct_name}...")

            # Get average volume for ratio calc
            avg_vol = get_avg_volume(sym)

            # Position details
            pos = get_position_in(sym, acct_name, portfolio)

            # Run opening range confirmation
            confirmation = confirm_signal(
                symbol          = sym,
                eod_signal      = sig.get("signal", "HOLD"),
                eod_conviction  = sig.get("conviction", 50),
                prev_close      = sig.get("price", 0),  # EOD price = prev close
                avg_daily_volume= avg_vol,
                above_sma50     = sig.get("above_sma50", False),
                above_sma200    = sig.get("above_sma200", False),
                scorecard       = sig.get("scorecard", {}),
                account_name    = acct_name,
                acct_value      = acct_value,
                suggested_usd   = sig.get("suggested_usd", 0),
                shares_held     = int(pos["shares"]) if pos else 0,
                avg_cost        = pos.get("avg_cost", 0) if pos else 0,
            )

            action = confirmation["action"]
            logger.info(f"    → {action}: {confirmation['reasoning'][:80]}")

            if action == "EXECUTE NOW":
                execute_list.append(confirmation)
            elif action == "STAND DOWN":
                stand_down_list.append(confirmation)
            elif action in ("WAIT",):
                wait_list.append(confirmation)

    # Sort execute list by conviction descending
    execute_list.sort(key=lambda x: -signal_log.get(
        f"{x.get('account','')}:{x['symbol']}", {}).get("conviction", 0))

    opening_narrative = _ollama_opening_narrative(
        ollama_ok, execute_list, wait_list, stand_down_list
    )

    # Fetch live market prices (mode="close" = actual prices, not futures)
    # At 9:50 AM the market is open so regularMarketPrice = live intraday price
    logger.info("Fetching live market snapshot...")
    try:
        open_snap    = get_futures_snapshot(force=True, mode="close")
        top_headline = get_top_headline()
        fetched = sum(1 for f in open_snap if f.get("price") is not None)
        logger.info(f"Market snapshot: {fetched}/8 tickers")
    except Exception as e:
        logger.warning(f"Market snapshot failed: {e}")
        open_snap    = []
        top_headline = {}

    html_report = build_opening_html(
        today_str, execute_list, wait_list, stand_down_list,
        opening_narrative, total_value,
        futures_snap=open_snap, top_headline=top_headline,
    )
    text_report = build_opening_text(
        execute_list, wait_list, stand_down_list,
        opening_narrative, today_str, total_value,
        futures_snap=open_snap,
    )

    exec_count = len(execute_list)
    subject    = (f"SWING SIGNAL: Opening {today_str} {datetime.now().strftime('%H:%M')} — "
                  f"{'EXECUTE: ' + ', '.join(t['symbol'] for t in execute_list) if execute_list else 'No confirmed trades'}")

    logger.info(f"\n{text_report}")
    deliver_report(subject, html_report, text_report)

    # Rich Discord BUY alert per confirmed EXECUTE NOW signal
    if execute_list:
        try:
            from notifications.discord import send_buy_alert
            _today = today_str
            for conf in execute_list:
                sym  = conf.get("symbol", "")
                acct = conf.get("account", "")
                # Look up signal details from signal_log
                _sig  = signal_log.get(f"{acct}:{sym}", signal_log.get(sym, {}))
                _sc   = _sig.get("scorecard", {})
                send_buy_alert(
                    symbol        = sym,
                    account       = acct,
                    signal        = _sig.get("signal", "BUY"),
                    conviction    = int(_sig.get("conviction", 0)),
                    price         = float(conf.get("entry_price", _sig.get("price", 0))),
                    entry_price   = float(conf.get("entry_price", _sig.get("price", 0))),
                    stop_price    = float(conf.get("stop_price",  0)),
                    target_price  = float(conf.get("target_price", 0)),
                    suggested_usd = float(_sig.get("suggested_usd", 0)),
                    rsi           = float(_sc.get("rsi", _sig.get("rsi", 50))),
                    vol_ratio     = float(_sc.get("vol_ratio", 1.0)),
                    above_sma50   = bool(_sc.get("above_sma50", True)),
                    above_sma200  = bool(_sc.get("above_sma200", True)),
                    ai_reasoning  = str(_sig.get("ai_reasoning", ""))[:200],
                    narrative     = str(_sig.get("narrative", ""))[:200],
                    today_str     = _today,
                )
        except Exception as _de:
            logger.debug(f"Discord BUY alert skipped: {_de}")

    logger.info(f"Opening report done. {exec_count} trades to execute. Log: {_log_file}")


if __name__ == "__main__":
    run()