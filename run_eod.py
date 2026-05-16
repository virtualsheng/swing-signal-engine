"""
run_eod.py — EOD Signal Report (4:15 PM ET)
─────────────────────────────────────────────
THE SOURCE OF TRUTH. Runs after market close using official
closing prices. Generates BUY/SELL/HOLD signals for all symbols
across all four accounts. Results saved to cache for morning reports.

This is NOT the actionable trade list — it sets up tomorrow's trades.
The 9:50 AM opening report (run_opening.py) tells you what to execute.

Schedule: 4:15 PM ET daily (Mon–Fri)
  python run_eod.py

Output:
  - Email: full HTML report with dashboard, narrative, scorecards
  - Telegram: condensed text summary
  - cache/signal_log.json: signals saved for morning reports to read
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

_log_file = f"logs/eod_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

from signals.data_fetcher    import fetch_batch, get_spy_closes
from signals.signal_engine   import get_technical_signal
from signals.ai_engine       import (
    check_ollama_available, grade_swing_setup,
    detect_market_regime, generate_signal_narrative,
    generate_market_narrative,
)
from signals.earnings_filter import is_near_earnings, clear_cache as clear_earnings
from signals.portfolio       import (
    load_portfolio, get_tradeable_accounts, get_monitor_accounts,
    is_held_in, get_position_in, is_in_cooldown,
    suggest_position_size,
)
from signals.report_builder  import (
    build_text_report, build_html_report,
    build_portfolio_summary, get_top_movers,
)
from notifications.notifier  import deliver_report

SIGNAL_LOG_FILE = "cache/signal_log.json"


def load_signal_log() -> dict:
    try:
        with open(SIGNAL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_signal_log(log: dict):
    with open(SIGNAL_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2, default=str)


def process_account(
    account_name: str,
    account_config: dict,
    bars: dict,
    ollama_ok: bool,
    signal_log: dict,
    today_str: str,
    portfolio: dict,
) -> list[dict]:
    signals       = []
    acct_value    = account_config.get("account_value", 100_000)
    min_conv      = account_config.get("min_conviction", 65)
    ai_min_conf   = account_config.get("ai_min_confidence", 0.55)
    cooldown_days = account_config.get("cooldown_days", 60)
    force_sell_c  = account_config.get("force_sell_conviction", 85)
    asset_class   = account_config.get("asset_class", "etf")
    symbols       = list(account_config.get("positions", {}).keys())

    logger.info(f"  {account_name}: {len(symbols)} symbols | ${acct_value:,.0f}")

    for symbol in symbols:
        if symbol not in bars:
            logger.debug(f"    {symbol}: no data")
            continue

        df         = bars[symbol]
        tech       = get_technical_signal(symbol, df)
        signal     = tech["signal"]
        conviction = tech["conviction"]
        price      = tech["price"]
        held       = is_held_in(symbol, account_name, portfolio)

        ai = grade_swing_setup(
            symbol=symbol, signal=signal, conviction=conviction, price=price,
            rsi=tech["rsi"], above_sma50=tech["above_sma50"],
            above_sma200=tech["above_sma200"], vol_ratio=tech["vol_ratio"],
            ema_cross=tech["ema_cross"], reason=tech["reason"],
            recent_prices=df["close"].tail(25).tolist(),
            portfolio_value=acct_value,
        ) if ollama_ok else {
            "confidence": conviction/100, "size_mult": 1.0,
            "action": signal, "reasoning": "Ollama unavailable.",
        }

        ai_confidence = ai["confidence"]
        ai_action     = ai.get("action", signal)
        ai_reasoning  = ai.get("reasoning", "")

        blocked_by = ""
        if signal == "BUY" and is_near_earnings(symbol):
            blocked_by = "near earnings"
        elif signal == "SELL" and not held:
            blocked_by = "not held"
        elif signal == "SELL" and held:
            in_cd = is_in_cooldown(account_name, symbol, cooldown_days)
            force = conviction >= force_sell_c and ai_action in ("SELL", "STRONG_SELL")
            if in_cd and not force:
                blocked_by = f"cooldown ({cooldown_days}d)"
        elif signal == "BUY" and not blocked_by:
            if conviction < min_conv:
                blocked_by = f"conviction {conviction} < {min_conv}"
            elif ai_confidence < ai_min_conf:
                blocked_by = f"AI {ai_confidence:.0%} < {ai_min_conf:.0%}"

        sizing = suggest_position_size(conviction, ai_confidence, acct_value)

        narrative = ""
        if not blocked_by and signal in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL") and ollama_ok:
            narrative = generate_signal_narrative(
                symbol=symbol, signal=signal, action=ai_action,
                confidence=ai_confidence, conviction=conviction,
                reasoning=ai_reasoning, price=price,
                suggested_size_usd=sizing["suggested_usd"],
                portfolio_value=acct_value,
            )

        pos = get_position_in(symbol, account_name, portfolio)
        unrealized_pct = None
        if pos and pos.get("avg_cost") and pos["avg_cost"] > 0:
            unrealized_pct = round((price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2)

        entry = {
            "account_name":   account_name,
            "account_value":  acct_value,
            "asset_class":    asset_class,
            "symbol":         symbol,
            "signal":         signal,
            "ai_action":      ai_action,
            "conviction":     conviction,
            "ai_confidence":  ai_confidence,
            "bear_score":     tech["bear_score"],
            "price":          price,
            "chg_1d":         tech.get("chg_1d", 0),
            "chg_5d":         tech.get("chg_5d", 0),
            "rsi":            tech["rsi"],
            "above_sma50":    tech["above_sma50"],
            "above_sma200":   tech["above_sma200"],
            "vol_ratio":      tech["vol_ratio"],
            "ema_cross":      tech["ema_cross"],
            "reason":         tech["reason"],
            "scorecard":      tech.get("scorecard", {}),
            "ai_reasoning":   ai_reasoning,
            "narrative":      narrative,
            "held":           held,
            "shares":         pos["shares"] if pos else 0,
            "avg_cost":       pos["avg_cost"] if pos else None,
            "unrealized_pct": unrealized_pct,
            "blocked_by":     blocked_by,
            "suggested_usd":  sizing["suggested_usd"],
            "suggested_pct":  sizing["suggested_pct"],
            "date":           today_str,
        }
        signals.append(entry)
        signal_log[f"{account_name}:{symbol}"] = entry
        logger.info(
            f"    {symbol:6} {signal:5} cv={conviction:3d} "
            f"AI={ai_confidence:.0%} → {'BLOCKED:'+blocked_by if blocked_by else 'OK'}"
        )

    return signals


def run():
    logger.info("=== EOD Signal Engine — 4:15 PM ===")

    portfolio   = load_portfolio()
    tradeable   = get_tradeable_accounts(portfolio)
    monitored   = get_monitor_accounts(portfolio)
    today_str   = datetime.today().strftime("%Y-%m-%d")
    signal_log  = load_signal_log()
    total_value = sum(v.get("account_value", 0)
                      for v in portfolio.get("accounts", {}).values())

    logger.info(f"Portfolio: ${total_value:,.0f}")
    clear_earnings()

    ollama_ok = check_ollama_available()
    logger.info(f"Ollama: {'ready' if ollama_ok else 'unavailable'}")

    all_symbols = set()
    for acct_config in tradeable.values():
        all_symbols.update(acct_config.get("positions", {}).keys())
    all_symbols.add("SPY")

    logger.info(f"Fetching data for {len(all_symbols)} symbols...")
    bars = fetch_batch(list(all_symbols), force=True)  # force fresh data at EOD
    logger.info(f"Data: {len(bars)}/{len(all_symbols)} symbols fetched")

    spy_closes = get_spy_closes(20)
    regime     = detect_market_regime(spy_closes)
    logger.info(f"Regime: {regime['regime']} | {regime['bias']}")

    all_signals_by_account = {}
    for account_name, account_config in tradeable.items():
        logger.info(f"\nProcessing: {account_name}")
        all_signals_by_account[account_name] = process_account(
            account_name, account_config, bars,
            ollama_ok, signal_log, today_str, portfolio,
        )

    save_signal_log(signal_log)

    monitor_data = {
        name: {
            "account_value": cfg.get("account_value", 0),
            "notes":         cfg.get("notes", ""),
            "positions":     cfg.get("positions", {}),
        }
        for name, cfg in monitored.items()
    }

    action_count = sum(
        1 for sigs in all_signals_by_account.values()
        for s in sigs
        if s["signal"] in ("BUY","SELL","STRONG_BUY","STRONG_SELL") and not s["blocked_by"]
    )

    portfolio_summary = build_portfolio_summary(all_signals_by_account, portfolio)
    top_movers        = get_top_movers(all_signals_by_account, n=5)
    market_narrative  = generate_market_narrative(
        regime=regime, portfolio_summary=portfolio_summary,
        top_movers=top_movers, report_type="EOD",
    ) if ollama_ok else ""

    text_report = build_text_report(
        all_signals_by_account, monitor_data, regime, "EOD", total_value,
        market_narrative=market_narrative, portfolio_summary=portfolio_summary,
        portfolio=portfolio,
    )
    html_report = build_html_report(
        all_signals_by_account, monitor_data, regime, "EOD", total_value,
        market_narrative=market_narrative, portfolio_summary=portfolio_summary,
        portfolio=portfolio,
    )

    subject = (f"SWING SIGNAL: EOD {today_str} — "
               f"{action_count} signal{'s' if action_count != 1 else ''} for tomorrow")
    logger.info(f"\n{text_report}")
    deliver_report(subject, html_report, text_report)
    logger.info(f"EOD done. Log: {_log_file}")


if __name__ == "__main__":
    run()