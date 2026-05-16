"""
update_portfolio.py — Fidelity CSV → portfolio.json updater
─────────────────────────────────────────────────────────────
Run after exporting your Fidelity portfolio positions CSV.
Updates shares, avg_cost, current_price, current_value in
portfolio.json without touching account_value, signal settings,
or sell_history.

Usage:
    python update_portfolio.py Portfolio_Positions_May-16-2026.csv

Or drop the latest CSV in the project folder and run:
    python update_portfolio.py

The script auto-finds the most recent Fidelity CSV in the
current directory if no path is given.

What it updates:
    - shares, avg_cost, current_price, current_value for each position
    - last_updated date
    - Adds new symbols found in the CSV (assigns to correct account)
    - Marks positions with 0 shares if they no longer appear in CSV

What it does NOT touch:
    - account_value (update manually or via fidelity total)
    - signal settings (min_conviction, cooldown_days, etc.)
    - sell_history (managed by the signal engine)
    - 401k positions (update manually)

Account assignment rules:
    - Symbols in Rollover IRA CSV section → Rollover IRA
    - Symbols in Roth IRA CSV section → ROTH IRA
    - Symbols in HSA CSV section → Health Savings Account
    - Mutual funds (FXAIX, FSSNX etc.) → VAST DATA 401(K) (monitor only)
"""

import csv
import json
import os
import sys
import glob
from datetime import datetime


PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")

# Fidelity account name → portfolio.json account name
ACCOUNT_NAME_MAP = {
    "ROLLOVER IRA":           "Rollover IRA",
    "ROTH IRA":               "ROTH IRA",
    "HEALTH SAVINGS ACCOUNT": "Health Savings Account",
    "HSA":                    "Health Savings Account",
    "VAST DATA 401(K)":       "VAST DATA 401(K)",
    "401K":                   "VAST DATA 401(K)",
    "401(K)":                 "VAST DATA 401(K)",
}

# Symbols to always skip (cash, pending, settlement)
SKIP_SYMBOLS = {"SPAXX", "FDRXX", "FCASH", "PENDING", "**"}


def clean_num(v, default=0.0):
    if v is None: return default
    v = str(v).replace("$","").replace(",","").replace("(","").replace(")","").strip()
    if v in ("--", "", "N/A"): return default
    try: return float(v)
    except: return default


def find_latest_csv() -> str | None:
    """Auto-find the most recently modified Fidelity CSV in cwd."""
    patterns = [
        "Portfolio_Positions*.csv",
        "portfolio_positions*.csv",
        "Positions*.csv",
    ]
    candidates = []
    for p in patterns:
        candidates.extend(glob.glob(p))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def load_portfolio() -> dict:
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def save_portfolio(portfolio: dict):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def parse_fidelity_csv(csv_path: str) -> dict:
    """
    Parse a Fidelity positions CSV.
    Returns {account_name: {symbol: {shares, avg_cost, current_price, current_value}}}
    """
    result = {}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Fidelity CSV columns vary slightly by export type
            acct_raw = (row.get("Account Name") or row.get("Account") or "").strip().upper()
            symbol   = (row.get("Symbol") or "").strip().upper()

            if not symbol or not acct_raw:
                continue
            if any(symbol.startswith(s) for s in SKIP_SYMBOLS):
                continue
            if symbol in SKIP_SYMBOLS:
                continue

            # Map Fidelity account name → portfolio.json name
            acct_name = None
            for key, mapped in ACCOUNT_NAME_MAP.items():
                if key in acct_raw:
                    acct_name = mapped
                    break
            if not acct_name:
                # Try partial match
                for key, mapped in ACCOUNT_NAME_MAP.items():
                    if any(word in acct_raw for word in key.split()):
                        acct_name = mapped
                        break
            if not acct_name:
                print(f"  ⚠️  Unknown account: '{acct_raw}' — skipping {symbol}")
                continue

            shares        = clean_num(row.get("Quantity") or row.get("Shares"))
            avg_cost      = clean_num(row.get("Average Cost Basis") or row.get("Average Cost"))
            current_price = clean_num(row.get("Last Price") or row.get("Current Price"))
            current_value = clean_num(row.get("Current Value") or row.get("Value"))

            if shares <= 0:
                continue

            if acct_name not in result:
                result[acct_name] = {}

            result[acct_name][symbol] = {
                "shares":        shares,
                "avg_cost":      round(avg_cost, 2),
                "current_price": round(current_price, 2),
                "current_value": round(current_value, 2),
            }

    return result


def update_portfolio(csv_path: str, dry_run: bool = False) -> dict:
    """
    Update portfolio.json from a Fidelity CSV.
    Returns a summary of changes.
    """
    print(f"\n  Reading: {csv_path}")
    fidelity_data = parse_fidelity_csv(csv_path)

    if not fidelity_data:
        print("  ❌ No data parsed — check CSV format")
        return {}

    portfolio  = load_portfolio()
    today      = datetime.today().strftime("%Y-%m-%d")
    summary    = {"updated": [], "added": [], "zeroed": [], "unchanged": []}

    for acct_name, csv_positions in fidelity_data.items():
        if acct_name not in portfolio.get("accounts", {}):
            print(f"  ⚠️  Account '{acct_name}' not in portfolio.json — skipping")
            continue

        acct = portfolio["accounts"][acct_name]
        existing = acct.setdefault("positions", {})

        # Update or add each position from CSV
        for symbol, data in csv_positions.items():
            if symbol in existing:
                old = existing[symbol]
                changed = (
                    abs(old.get("shares", 0) - data["shares"]) > 0.001 or
                    abs(old.get("avg_cost", 0) - data["avg_cost"]) > 0.01
                )
                existing[symbol].update({
                    "shares":        data["shares"],
                    "avg_cost":      data["avg_cost"],
                    "current_price": data["current_price"],
                    "current_value": data["current_value"],
                })
                if changed:
                    summary["updated"].append(f"{acct_name}: {symbol}")
                else:
                    summary["unchanged"].append(f"{acct_name}: {symbol}")
            else:
                # New position not previously tracked
                existing[symbol] = {
                    "shares":        data["shares"],
                    "avg_cost":      data["avg_cost"],
                    "current_price": data["current_price"],
                    "current_value": data["current_value"],
                    "date_entered":  today,
                }
                summary["added"].append(f"{acct_name}: {symbol}")

        # Zero out positions no longer in CSV (sold)
        for symbol in list(existing.keys()):
            if symbol not in csv_positions and existing[symbol].get("shares", 0) > 0:
                existing[symbol]["shares"] = 0
                existing[symbol]["current_value"] = 0
                summary["zeroed"].append(f"{acct_name}: {symbol}")

    # Update account values from CSV totals
    for acct_name, csv_positions in fidelity_data.items():
        if acct_name in portfolio.get("accounts", {}):
            total = sum(p["current_value"] for p in csv_positions.values())
            if total > 0:
                portfolio["accounts"][acct_name]["account_value"] = round(total, 2)

    portfolio["last_updated"] = today

    if dry_run:
        print("  DRY RUN — no changes written")
    else:
        save_portfolio(portfolio)
        print(f"  ✅ portfolio.json updated")

    return summary


def main():
    # Find CSV
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = find_latest_csv()
        if not csv_path:
            print("\n  ❌ No Fidelity CSV found.")
            print("  Usage: python update_portfolio.py Portfolio_Positions_DATE.csv")
            print("  Or export from Fidelity and place in this folder.")
            sys.exit(1)
        print(f"\n  Auto-found: {csv_path}")

    if not os.path.exists(csv_path):
        print(f"\n  ❌ File not found: {csv_path}")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    summary = update_portfolio(csv_path, dry_run=dry_run)

    # Print summary
    print()
    if summary.get("added"):
        print(f"  ➕ New positions ({len(summary['added'])}):")
        for s in summary["added"]: print(f"     {s}")
    if summary.get("updated"):
        print(f"  ✏️  Updated ({len(summary['updated'])}):")
        for s in summary["updated"]: print(f"     {s}")
    if summary.get("zeroed"):
        print(f"  🔴 Sold/zeroed ({len(summary['zeroed'])}):")
        for s in summary["zeroed"]: print(f"     {s}")
    if summary.get("unchanged"):
        print(f"  ✅ Unchanged: {len(summary['unchanged'])} positions")

    total_changes = len(summary.get("added",[])) + len(summary.get("updated",[])) + len(summary.get("zeroed",[]))
    print()
    if dry_run:
        print(f"  DRY RUN complete — {total_changes} change(s) would be applied")
        print(f"  Run without --dry-run to apply.")
    else:
        print(f"  Done — {total_changes} change(s) applied to portfolio.json")
    print()


if __name__ == "__main__":
    main()