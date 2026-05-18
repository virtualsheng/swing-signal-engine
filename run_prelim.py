"""
run_prelim.py — Swing Signal Engine
─────────────────────────────────────────────────────────────────────
3:50 PM PRELIMINARY report — runs 10 minutes before market close on
near-close prices. Gives you a window to act before 4 PM if a signal
is strong enough to trade today rather than waiting until tomorrow.

Schedule (Windows Task Scheduler):
  3:50 PM ET  → python run_prelim.py

What it does:
  - Runs the same technical signal engine as run_eod.py
  - Uses near-close prices (10 min before official close)
  - Sends a concise email + Telegram alert: only STRONG signals
    (BUY/SELL conviction >= SWING_MIN_CONVICTION) are highlighted
  - Does NOT overwrite signal_log.json — that's run_eod.py's job
  - Does NOT run AI grading (too slow for a 10-min window)
  - Flags anything that changed since yesterday's EOD signal

Subject line: SWING SIGNAL: PRELIM 3:50 PM — ACT NOW: SMH, NVDA | WATCH: QQQ
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

from signals.data_fetcher  import fetch_batch
from signals.signal_engine import get_technical_signal
from signals.portfolio     import load_portfolio, get_tradeable_accounts
from signals.expected_move import get_expected_move
from notifications.notifier import deliver_report

SIGNAL_LOG_FILE  = "cache/signal_log.json"
MIN_CONVICTION   = int(os.getenv("SWING_MIN_CONVICTION", "65"))
TODAY_STR        = datetime.today().strftime("%Y-%m-%d")


# ── helpers ────────────────────────────────────────────────────────────────

def load_signal_log() -> dict:
    try:
        with open(SIGNAL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def load_symbols_for_account(account_name: str) -> list[str]:
    """Read symbols from symbols.txt for this account section."""
    symbols_file = os.path.join(os.path.dirname(__file__), "symbols.txt")
    if not os.path.exists(symbols_file):
        return []
    symbols, in_section = [], False
    with open(symbols_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                in_section = account_name.lower() in line.lower()
                continue
            if in_section and line.upper() == line:
                symbols.append(line)
    return symbols


def signal_label(action: str) -> str:
    return {"BUY": "BUY", "STRONG_BUY": "STRONG BUY",
            "SELL": "SELL", "STRONG_SELL": "STRONG SELL",
            "HOLD": "hold"}.get(action, action)


def signal_emoji(action: str) -> str:
    return {"BUY": "🟢", "STRONG_BUY": "🟢🟢",
            "SELL": "🔴", "STRONG_SELL": "🔴🔴",
            "HOLD": "⚪"}.get(action, "⚪")


def changed_since_eod(symbol: str, new_action: str, signal_log: dict) -> str:
    """Return change indicator if signal differs from last EOD."""
    key = f"*:{symbol}"  # signal_log keys are "account:symbol"
    # search any account entry for this symbol
    for k, v in signal_log.items():
        if k.endswith(f":{symbol}"):
            prev = v.get("signal", "HOLD")
            if prev != new_action:
                return f"  ← was {prev}"
            return ""
    return "  ← NEW"


# ── main ───────────────────────────────────────────────────────────────────

def run():
    logger.info("=== PRELIM Signal Run — 3:50 PM ===")

    portfolio  = load_portfolio()
    tradeable  = get_tradeable_accounts(portfolio)
    signal_log = load_signal_log()

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

    # Fetch near-close bars (same as EOD — yfinance returns latest available)
    bars = fetch_batch(list(all_symbols.keys()))

    # ── Run technical signals ──────────────────────────────────────────────
    act_now, watch, holds = [], [], []

    for symbol, acct_name in sorted(all_symbols.items()):
        if symbol not in bars or bars[symbol] is None or bars[symbol].empty:
            logger.warning(f"  {symbol}: no data")
            continue
        try:
            result     = get_technical_signal(symbol, bars[symbol])
            action     = result.get("action", "HOLD")
            conviction = result.get("bull_score", 0) + abs(result.get("bear_score", 0))
            conviction = min(int(conviction), 99)
            change     = changed_since_eod(symbol, action, signal_log)

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

    # Sort each list by conviction descending
    act_now.sort(key=lambda x: x["conviction"], reverse=True)
    watch.sort(key=lambda x: x["conviction"], reverse=True)

    logger.info(f"ACT NOW: {len(act_now)} | WATCH: {len(watch)} | HOLD: {len(holds)}")

    # ── Expected move (SPY + QQQ) ──────────────────────────────────────────
    em_lines = []
    for sym in ("SPY", "QQQ"):
        try:
            em = get_expected_move(sym)
            if em:
                em_lines.append(
                    f"{sym}  daily EM ±${em['daily_em']:.2f} ({em['daily_em_pct']:.1f}%)  "
                    f"[${em['daily_lower']:.2f} – ${em['daily_upper']:.2f}]"
                )
        except Exception:
            pass

    # ── Build report ───────────────────────────────────────────────────────
    now_str = datetime.now().strftime("%I:%M %p")
    act_syms  = [e["symbol"] for e in act_now]
    watch_syms = [e["symbol"] for e in watch]

    subject_parts = []
    if act_syms:
        subject_parts.append(f"ACT NOW: {', '.join(act_syms[:4])}")
    if watch_syms:
        subject_parts.append(f"WATCH: {', '.join(watch_syms[:4])}")
    if not subject_parts:
        subject_parts.append("No actionable signals")

    subject = f"SWING SIGNAL: PRELIM {now_str} — {' | '.join(subject_parts)}"

    # ── Text body ──────────────────────────────────────────────────────────
    lines = [
        "=" * 62,
        f"  PRELIMINARY SIGNALS — {now_str} ET ({TODAY_STR})",
        "  Near-close prices  |  No AI grading  |  10 min to act",
        "=" * 62,
        "",
    ]

    if em_lines:
        lines += ["OPTIONS EXPECTED MOVE (today's range):", *em_lines, ""]

    if act_now:
        lines.append(f"{'ACT NOW':─<62}")
        lines.append(f"  {'SYMBOL':<8} {'SIGNAL':<14} {'CV':>4}  {'ACCOUNT':<18}  NOTE")
        for e in act_now:
            lines.append(
                f"  {e['symbol']:<8} "
                f"{signal_emoji(e['action'])} {signal_label(e['action']):<12} "
                f"{e['conviction']:>4}  "
                f"{e['account']:<18}"
                f"{e['change']}"
            )
        lines.append("")

    if watch:
        lines.append(f"{'WATCH (below min conviction)':─<62}")
        lines.append(f"  {'SYMBOL':<8} {'SIGNAL':<14} {'CV':>4}  {'ACCOUNT':<18}  NOTE")
        for e in watch:
            lines.append(
                f"  {e['symbol']:<8} "
                f"{signal_emoji(e['action'])} {signal_label(e['action']):<12} "
                f"{e['conviction']:>4}  "
                f"{e['account']:<18}"
                f"{e['change']}"
            )
        lines.append("")

    lines += [
        f"{'HOLDS':─<62}",
        "  " + "  ".join(
            f"{e['symbol']}({e['conviction']})" for e in holds[:20]
        ),
        "",
        "─" * 62,
        "  ⚠️  Near-close prices — may differ slightly from official close.",
        "  Official EOD signals arrive at ~4:15 PM via run_eod.py.",
        "  Only act on STRONG signals with conviction >= "
        f"{MIN_CONVICTION}.",
        "─" * 62,
    ]

    text_report = "\n".join(lines)
    html_report = ""

    # ── Deliver ────────────────────────────────────────────────────────────
    logger.info(f"Sending: {subject}")
    deliver_report(subject, html_report, text_report)
    logger.info("Done.")


if __name__ == "__main__":
    run()