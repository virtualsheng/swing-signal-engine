"""
earnings_filter.py — Block signals within 48h of earnings reports
──────────────────────────────────────────────────────────────────
Uses Yahoo Finance earnings calendar. ETFs have no earnings dates
and are automatically passed through.
"""

import logging
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)

_earnings_cache: dict[str, list[datetime]] = {}

ETF_SUFFIXES = {"QQQ","SPY","IWM","EFA","GLD","SLV","TLT","HYG","XLK","XLF",
                "XLE","XLV","XLI","XLC","XLU","XLRE","XLP","XLB","XLY","SMH",
                "SOXX","ARKK","TQQQ","SQQQ","SPXL","SPXS","SOXL","SOXS",
                "IBIT","BITI","GLDM","PSLV","DBC","DBMF","GDE","GRID","SPMO",
                "NANR","QQQM","EWJV","EWT","EWY","URA","URNM","GDXJ","GDMN",
                "REMX","ROBO","SLVP","UFO","DRAM","VUG","AVUV","VOO","VTI",
                "VEA","VWO","BND","AGG","LQD","VIG","VYM","SCHD","IVV",
                "IJH","IJR","VTIP","VCIT","VCSH","BSV","VXUS"}


def _is_etf(symbol: str) -> bool:
    return symbol.upper() in ETF_SUFFIXES


def get_earnings_dates(symbol: str) -> list[datetime]:
    if symbol in _earnings_cache:
        return _earnings_cache[symbol]
    if _is_etf(symbol):
        _earnings_cache[symbol] = []
        return []
    try:
        cal = yf.Ticker(symbol).calendar
        dates = []
        if cal is not None and not cal.empty:
            for col in cal.columns:
                if "earnings" in col.lower() or "date" in col.lower():
                    for v in cal[col]:
                        try:
                            dates.append(pd.Timestamp(v).to_pydatetime())
                        except Exception:
                            pass
        _earnings_cache[symbol] = dates
        return dates
    except Exception:
        _earnings_cache[symbol] = []
        return []


def is_near_earnings(symbol: str, buffer_hours: int = 48) -> bool:
    """Return True if earnings are within buffer_hours of now."""
    if _is_etf(symbol):
        return False
    try:
        import pandas as pd
        dates = get_earnings_dates(symbol)
        now   = datetime.utcnow()
        for d in dates:
            if d.tzinfo:
                d = d.replace(tzinfo=None)
            diff_h = abs((d - now).total_seconds()) / 3600
            if diff_h <= buffer_hours:
                logger.info(f"{symbol}: earnings in {diff_h:.0f}h — blocking signal")
                return True
        return False
    except Exception:
        return False


def clear_cache():
    _earnings_cache.clear()