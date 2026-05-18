"""
signals/auto_update_portfolio.py
─────────────────────────────────
Called automatically by run_eod.py and run_morning.py at startup.
Scans the project root for a Fidelity Portfolio CSV, and if one is
found that is newer than the last portfolio.json update, silently
applies it. No user interaction required.

CSV filename patterns detected:
  Portfolio_Positions*.csv
  portfolio_positions*.csv
  Positions*.csv
  Portfolio*.csv
"""

import csv
import glob
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT   = os.path.dirname(BASE_DIR)
PORTFOLIO_FILE = os.path.join(PROJECT_ROOT, "portfolio.json")

ACCOUNT_NAME_MAP = {
    "ROLLOVER IRA":           "Rollover IRA",
    "ROTH IRA":               "ROTH IRA",
    "HEALTH SAVINGS":         "Health Savings Account",
    "HSA":                    "Health Savings Account",
    "VAST DATA 401":          "VAST DATA 401(K)",
    "401K":                   "VAST DATA 401(K)",
    "401(K)":                 "VAST DATA 401(K)",
}
SKIP_SYMBOLS = {"SPAXX", "FDRXX", "FCASH", "PENDING", "**", ""}


def _clean_num(v, default=0.0) -> float:
    if v is None:
        return default
    v = str(v).replace("$","").replace(",","").replace("(","").replace(")","").strip()
    if v in ("--", "", "N/A", "n/a"):
        return default
    try:
        return float(v)
    except ValueError:
        return default


def find_latest_csv() -> str | None:
    """Find the most recently modified Fidelity CSV in the project root."""
    patterns = [
        "Portfolio_Positions*.csv",
        "portfolio_positions*.csv",
        "Positions*.csv",
        "Portfolio*.csv",
    ]
    candidates = []
    for p in patterns:
        candidates.extend(glob.glob(os.path.join(PROJECT_ROOT, p)))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _csv_is_newer_than_portfolio(csv_path: str) -> bool:
    """Return True if the CSV was modified after portfolio.json was last updated."""
    try:
        csv_mtime = os.path.getmtime(csv_path)
        with open(PORTFOLIO_FILE) as f:
            portfolio = json.load(f)
        last_updated_str = portfolio.get("last_updated", "2000-01-01")
        last_updated_ts  = datetime.strptime(last_updated_str, "%Y-%m-%d").timestamp()
        return csv_mtime > last_updated_ts
    except Exception:
        return True  # if in doubt, apply update


def _parse_csv(csv_path: str) -> dict:
    """Parse Fidelity positions CSV → {account_name: {symbol: {shares,...}}}"""
    result = {}
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                acct_raw = (row.get("Account Name") or row.get("Account") or "").strip().upper()
                symbol   = (row.get("Symbol") or "").strip().upper()

                if not symbol or symbol in SKIP_SYMBOLS:
                    continue
                if any(symbol.startswith(s) for s in SKIP_SYMBOLS):
                    continue

                # Map account name
                acct_name = None
                for key, mapped in ACCOUNT_NAME_MAP.items():
                    if key in acct_raw:
                        acct_name = mapped
                        break
                if not acct_name:
                    continue

                shares        = _clean_num(row.get("Quantity") or row.get("Shares"))
                avg_cost      = _clean_num(row.get("Average Cost Basis") or row.get("Average Cost"))
                current_price = _clean_num(row.get("Last Price") or row.get("Current Price"))
                current_value = _clean_num(row.get("Current Value") or row.get("Value"))

                if shares <= 0:
                    continue

                if acct_name not in result:
                    result[acct_name] = {}
                result[acct_name][symbol] = {
                    "shares":        round(shares, 4),
                    "avg_cost":      round(avg_cost, 2),
                    "current_price": round(current_price, 2),
                    "current_value": round(current_value, 2),
                }
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
    return result


def auto_update(silent: bool = False) -> dict:
    """
    Find the latest Fidelity CSV and apply it to portfolio.json if it's newer.
    Returns a summary dict: {applied: bool, csv_path: str, changes: int, skipped_reason: str}
    Called at the start of run_eod.py and run_morning.py.
    """
    csv_path = find_latest_csv()

    if not csv_path:
        if not silent:
            logger.info("Portfolio auto-update: no Fidelity CSV found in project root — skipped")
        return {"applied": False, "skipped_reason": "no CSV found"}

    csv_name = os.path.basename(csv_path)

    if not _csv_is_newer_than_portfolio(csv_path):
        if not silent:
            logger.info(f"Portfolio auto-update: {csv_name} is not newer than portfolio.json — skipped")
        return {"applied": False, "csv_path": csv_path, "skipped_reason": "CSV not newer than portfolio.json"}

    logger.info(f"Portfolio auto-update: applying {csv_name}...")

    fidelity_data = _parse_csv(csv_path)
    if not fidelity_data:
        logger.warning("Portfolio auto-update: CSV parsed 0 positions — skipped")
        return {"applied": False, "csv_path": csv_path, "skipped_reason": "CSV parsed 0 positions"}

    with open(PORTFOLIO_FILE) as f:
        portfolio = json.load(f)

    today   = datetime.today().strftime("%Y-%m-%d")
    changes = 0

    for acct_name, csv_positions in fidelity_data.items():
        if acct_name not in portfolio.get("accounts", {}):
            logger.warning(f"  Account '{acct_name}' not in portfolio.json — skipping")
            continue

        acct     = portfolio["accounts"][acct_name]
        existing = acct.setdefault("positions", {})

        for symbol, data in csv_positions.items():
            if symbol in existing:
                old = existing[symbol]
                if (abs(old.get("shares", 0) - data["shares"]) > 0.001 or
                        abs(old.get("avg_cost", 0) - data["avg_cost"]) > 0.01):
                    changes += 1
                existing[symbol].update(data)
            else:
                existing[symbol] = {**data, "date_entered": today}
                changes += 1
                logger.info(f"  New position: {acct_name} {symbol}")

        # Zero out positions no longer in CSV
        for symbol in list(existing.keys()):
            if symbol not in csv_positions and existing[symbol].get("shares", 0) > 0:
                existing[symbol]["shares"] = 0
                existing[symbol]["current_value"] = 0
                changes += 1
                logger.info(f"  Zeroed (sold): {acct_name} {symbol}")

        # Update account_value from CSV totals
        total = sum(p["current_value"] for p in csv_positions.values())
        if total > 0:
            portfolio["accounts"][acct_name]["account_value"] = round(total, 2)

    portfolio["last_updated"] = today

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

    logger.info(f"Portfolio auto-update: {changes} change(s) applied from {csv_name}")
    return {"applied": True, "csv_path": csv_path, "changes": changes}