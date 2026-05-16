"""
portfolio.py — Account-aware portfolio tracker
────────────────────────────────────────────────
Manages four separate accounts from portfolio.json:
  Rollover IRA   — ETFs,         $564k, min_conviction=65, cooldown=60d
  Roth IRA       — stocks,       $181k, min_conviction=70, cooldown=90d
  HSA            — ETFs,         $6.8k, min_conviction=65, cooldown=60d
  401(k)         — mutual funds, $2k,   monitor only (no signals)

sell_history.json is keyed as "account_type:SYMBOL" to prevent
cooldown in one account from blocking signals in another.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_FILE    = os.path.join(BASE_DIR, "..", "portfolio.json")
SELL_HISTORY_FILE = os.path.join(BASE_DIR, "..", "cache", "sell_history.json")

CONVICTION_SIZING = [
    (90, 0.07),
    (80, 0.05),
    (70, 0.03),
    (0,  0.02),
]

CONFIDENCE_MULT = [
    (0.85, 1.5),
    (0.70, 1.2),
    (0.55, 1.0),
    (0.0,  0.7),
]


def load_portfolio() -> dict:
    try:
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"portfolio.json not found at {PORTFOLIO_FILE}")
        return {"accounts": {}}
    except Exception as e:
        logger.error(f"Portfolio load error: {e}")
        return {"accounts": {}}


def get_tradeable_accounts(portfolio: dict) -> dict:
    return {k: v for k, v in portfolio.get("accounts", {}).items()
            if v.get("tradeable", False) and v.get("signal_mode") == "full"}


def get_monitor_accounts(portfolio: dict) -> dict:
    return {k: v for k, v in portfolio.get("accounts", {}).items()
            if v.get("signal_mode") == "monitor_only"}


def is_held_in(symbol: str, account_name: str, portfolio: dict) -> bool:
    acct = portfolio.get("accounts", {}).get(account_name, {})
    pos  = acct.get("positions", {}).get(symbol.upper(), {})
    return pos.get("shares", 0) > 0


def get_position_in(symbol: str, account_name: str, portfolio: dict) -> dict | None:
    acct = portfolio.get("accounts", {}).get(account_name, {})
    pos  = acct.get("positions", {}).get(symbol.upper())
    if pos and pos.get("shares", 0) > 0:
        return pos
    return None


def _sell_key(account_name: str, symbol: str) -> str:
    return f"{account_name.lower().replace(' ','_')}:{symbol.upper()}"


def load_sell_history() -> dict:
    try:
        with open(SELL_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_sell_history(history: dict):
    os.makedirs(os.path.dirname(SELL_HISTORY_FILE), exist_ok=True)
    with open(SELL_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def record_sell(account_name: str, symbol: str):
    history = load_sell_history()
    history[_sell_key(account_name, symbol)] = datetime.today().strftime("%Y-%m-%d")
    save_sell_history(history)


def is_in_cooldown(account_name: str, symbol: str, cooldown_days: int) -> bool:
    history = load_sell_history()
    last = history.get(_sell_key(account_name, symbol))
    if not last:
        return False
    try:
        elapsed = (datetime.today() - datetime.strptime(last, "%Y-%m-%d")).days
        return elapsed < cooldown_days
    except Exception:
        return False


def suggest_position_size(conviction: int, ai_confidence: float, account_value: float) -> dict:
    base_pct = 0.02
    for min_conv, pct in CONVICTION_SIZING:
        if conviction >= min_conv:
            base_pct = pct
            break
    size_mult = 1.0
    for min_conf, mult in CONFIDENCE_MULT:
        if ai_confidence >= min_conf:
            size_mult = mult
            break
    suggested_usd = account_value * base_pct * size_mult
    return {
        "base_pct":      base_pct,
        "size_mult":     size_mult,
        "suggested_usd": round(suggested_usd, 0),
        "suggested_pct": round(suggested_usd / account_value * 100, 2),
    }