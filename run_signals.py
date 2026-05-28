"""
run_signals.py — Swing Signal Engine v2 (account-aware)
─────────────────────────────────────────────────────────
Generates daily signals for all four Fidelity accounts and delivers
a unified report via email + Telegram. No order execution.

Accounts:
  Rollover IRA   $564k  — 18 ETFs,          full signals
  Roth IRA       $181k  — 13 stocks,         full signals (higher threshold)
  HSA            $6.8k  — 3 ETFs,            full signals
  401(k)         $2k    — 5 mutual funds,    monitor only

Schedule (Windows Task Scheduler):
  8:30 AM ET  → python run_signals.py premarket
  4:15 PM ET  → python run_signals.py eod
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

_log_file = f"logs/signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
    suggest_position_size, record_sell,
)
from signals.report_builder  import (build_text_report, build_html_report,
    build_portfolio_summary, get_top_movers)
from notifications.notifier  import deliver_report

SIGNAL_LOG_FILE = "cache/signal_log.json"


def load_symbols_for_account(account_config: dict) -> list[str]:
    """Extract symbols held in a specific account."""
    return list(account_config.get("positions", {}).keys())


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
    regime: dict,
    signal_log: dict,
    today_str: str,
) -> list[dict]:
    """
    Generate signals for all symbols in a single account.
    Returns list of signal dicts tagged with account_name.
    """
    signals       = []
    acct_value    = account_config.get("account_value", 100_000)
    min_conv      = account_config.get("min_conviction", 65)
    ai_min_conf   = account_config.get("ai_min_confidence", 0.55)
    cooldown_days = account_config.get("cooldown_days", 60)
    force_sell_c  = account_config.get("force_sell_conviction", 85)
    asset_class   = account_config.get("asset_class", "etf")

    symbols = load_symbols_for_account(account_config)
    logger.info(f"  {account_name}: {len(symbols)} symbols | ${acct_value:,.0f}")

    # Load a fresh portfolio for position checks
    portfolio = load_portfolio()

    for symbol in symbols:
        if symbol not in bars:
            logger.debug(f"    {symbol}: no data")
            continue

        df   = bars[symbol]
        tech = get_technical_signal(symbol, df)
        signal     = tech["signal"]
        conviction = tech["conviction"]
        price      = tech["price"]
        held       = is_held_in(symbol, account_name, portfolio)

        # AI grading
        ai = grade_swing_setup(
            symbol          = symbol,
            signal          = signal,
            conviction      = conviction,
            price           = price,
            rsi             = tech["rsi"],
            above_sma50     = tech["above_sma50"],
            above_sma200    = tech["above_sma200"],
            vol_ratio       = tech["vol_ratio"],
            ema_cross       = tech["ema_cross"],
            reason          = tech["reason"],
            recent_prices   = df["close"].tail(25).tolist(),
            portfolio_value = acct_value,
        )
        ai_confidence = ai["confidence"]
        ai_action     = ai.get("action", signal)
        ai_reasoning  = ai.get("reasoning", "")

        # Determine blocked_by reason
        blocked_by = ""

        if signal == "BUY" and is_near_earnings(symbol):
            blocked_by = "near earnings"

        elif signal == "SELL" and not held:
            blocked_by = "not held in this account"

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

        # Position sizing
        sizing = suggest_position_size(conviction, ai_confidence, acct_value)

        # Narrative for actionable signals
        narrative = ""
        if not blocked_by and signal in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
            narrative = generate_signal_narrative(
                symbol             = symbol,
                signal             = signal,
                action             = ai_action,
                confidence         = ai_confidence,
                conviction         = conviction,
                reasoning          = ai_reasoning,
                price              = price,
                suggested_size_usd = sizing["suggested_usd"],
                portfolio_value    = acct_value,
            )

        # Position context
        pos = get_position_in(symbol, account_name, portfolio)
        unrealized_pct = None
        if pos and pos.get("avg_cost") and pos["avg_cost"] > 0:
            unrealized_pct = round((price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2)

        entry = {
            "account_name":    account_name,
            "account_value":   acct_value,
            "asset_class":     asset_class,
            "symbol":          symbol,
            "signal":          signal,
            "ai_action":       ai_action,
            "conviction":      conviction,
            "ai_confidence":   ai_confidence,
            "bear_score":      tech["bear_score"],
            "price":           price,
            "chg_1d":          tech.get("chg_1d", 0),
            "chg_5d":          tech.get("chg_5d", 0),
            "rsi":             tech["rsi"],
            "above_sma50":     tech["above_sma50"],
            "above_sma200":    tech["above_sma200"],
            "vol_ratio":       tech["vol_ratio"],
            "ema_cross":       tech["ema_cross"],
            "reason":          tech["reason"],
            "scorecard":       tech.get("scorecard", {}),
            "ai_reasoning":    ai_reasoning,
            "narrative":       narrative,
            "held":            held,
            "shares":          pos["shares"] if pos else 0,
            "avg_cost":        pos["avg_cost"] if pos else None,
            "unrealized_pct":  unrealized_pct,
            "blocked_by":      blocked_by,
            "suggested_usd":   sizing["suggested_usd"],
            "suggested_pct":   sizing["suggested_pct"],
            "date":            today_str,
        }
        signals.append(entry)
        signal_log[f"{account_name}:{symbol}"] = entry

        status = f"BLOCKED:{blocked_by}" if blocked_by else "OK"
        logger.info(
            f"    {symbol:6} {signal:5} conv={conviction:3d} "
            f"AI={ai_confidence:.0%} held={held} → {status}"
        )

    return signals


def run(report_type: str = "EOD"):
    logger.info(f"=== Swing Signal Engine v2 — {report_type} ===")

    portfolio  = load_portfolio()
    tradeable  = get_tradeable_accounts(portfolio)
    monitored  = get_monitor_accounts(portfolio)
    today_str  = datetime.today().strftime("%Y-%m-%d")
    signal_log = load_signal_log()

    total_value = sum(v.get("account_value", 0)
                      for v in portfolio.get("accounts", {}).values())
    logger.info(f"Total portfolio: ${total_value:,.0f} across "
                f"{len(tradeable)} tradeable + {len(monitored)} monitor-only accounts")

    # Clear earnings cache
    clear_earnings()

    # Check Ollama
    ollama_ok = check_ollama_available()
    logger.info(f"Ollama: {'ready' if ollama_ok else 'unavailable — fallback mode'}")

    # Collect all tradeable symbols
    all_symbols = set()
    for acct_config in tradeable.values():
        all_symbols.update(acct_config.get("positions", {}).keys())
    # Also need SPY for regime
    all_symbols.add("SPY")

    logger.info(f"Fetching data for {len(all_symbols)} unique symbols...")
    bars = fetch_batch(list(all_symbols))
    logger.info(f"Data fetched: {len(bars)}/{len(all_symbols)} symbols")

    # Market regime
    spy_closes = get_spy_closes(20)
    regime     = detect_market_regime(spy_closes)
    logger.info(f"Market regime: {regime['regime']} | {regime['bias']}")

    # Process each tradeable account
    all_signals_by_account = {}
    for account_name, account_config in tradeable.items():
        logger.info(f"\nProcessing: {account_name}")
        acct_signals = process_account(
            account_name, account_config, bars,
            ollama_ok, regime, signal_log, today_str,
        )
        all_signals_by_account[account_name] = acct_signals

    save_signal_log(signal_log)

    # Monitor-only accounts — just pass position data
    monitor_data = {}
    for account_name, account_config in monitored.items():
        positions = account_config.get("positions", {})
        monitor_data[account_name] = {
            "account_value": account_config.get("account_value", 0),
            "notes":         account_config.get("notes", ""),
            "positions":     positions,
        }

    # Count total actionable signals
    action_count = sum(
        1 for acct_signals in all_signals_by_account.values()
        for s in acct_signals
        if s["signal"] in ("BUY","SELL","STRONG_BUY","STRONG_SELL") and not s["blocked_by"]
    )

    # Build portfolio summary + market narrative
    portfolio_summary = build_portfolio_summary(all_signals_by_account, portfolio)
    top_movers        = get_top_movers(all_signals_by_account, n=5)
    market_narrative  = generate_market_narrative(
        regime            = regime,
        portfolio_summary = portfolio_summary,
        top_movers        = top_movers,
        report_type       = report_type,
    ) if ollama_ok else ""

    # Build and deliver report
    text_report = build_text_report(
        all_signals_by_account, monitor_data, regime, report_type, total_value,
        market_narrative=market_narrative,
        portfolio_summary=portfolio_summary,
        portfolio=portfolio,
    )
    html_report = build_html_report(
        all_signals_by_account, monitor_data, regime, report_type, total_value,
        market_narrative=market_narrative,
        portfolio_summary=portfolio_summary,
        portfolio=portfolio,
    )

    label   = "Pre-Market" if report_type == "PREMARKET" else "EOD"
    subject = (f"SWING SIGNAL: {label} {today_str} — "
               f"{action_count} action{'s' if action_count != 1 else ''}")

    logger.info(f"\n{text_report}")
    logger.info(f"Delivering: {action_count} actionable signals across all accounts")
    deliver_report(subject, html_report, text_report)

    # Rich Discord EOD summary embed (only on EOD run, not premarket)
    if report_type == "EOD":
        try:
            from notifications.discord import send_eod_summary
            _buy_sigs, _sell_sigs, _hold_sigs = [], [], []
            for _acct_sigs in all_signals_by_account.values():
                for _s in _acct_sigs:
                    if _s.get("blocked_by"):
                        continue
                    _entry = {
                        "symbol":    _s["symbol"],
                        "account":   _s.get("account_name", ""),
                        "conviction":_s.get("conviction", 0),
                        "price":     _s.get("price", 0),
                        "reason":    _s.get("reason","")[:70],
                    }
                    sig = _s.get("signal","")
                    if "BUY"  in sig: _buy_sigs.append(_entry)
                    elif "SELL" in sig: _sell_sigs.append(_entry)
                    else: _hold_sigs.append(_entry)

            _spy_chg = 0.0
            _qqq_chg = 0.0
            try:
                import yfinance as _yf
                _spy_chg = float(_yf.Ticker("SPY").fast_info.get("regularMarketChangePercent", 0) or 0)
                _qqq_chg = float(_yf.Ticker("QQQ").fast_info.get("regularMarketChangePercent", 0) or 0)
            except Exception:
                pass

            send_eod_summary(
                today_str    = today_str,
                spy_chg      = _spy_chg,
                qqq_chg      = _qqq_chg,
                vix          = float(regime.get("vix", 0)) if isinstance(regime, dict) else 0.0,
                buy_signals  = _buy_sigs,
                sell_signals = _sell_sigs,
                hold_signals = _hold_sigs,
                narrative    = market_narrative[:400] if market_narrative else "",
                total_value  = float(total_value or 0),
            )
        except Exception as _de:
            logger.debug(f"Discord EOD summary skipped: {_de}")

    logger.info(f"Done. Log: {_log_file}")