"""
signals/market_futures.py — CNBC-style market snapshot + top headline
───────────────────────────────────────────────────────────────────────
Fetches 8 market indicators matching the CNBC top bar:
  DOW FUT  S&P FUT  NAS FUT  OIL  US 10-YR  GOLD  SILVER  BITCOIN

Two modes:
  mode="premarket" (morning report, default)
    - Index tickers: YM=F / ES=F / NQ=F (E-mini futures — update pre-market)
      Cash indices (^DJI/^GSPC/^IXIC) don't update until 9:30 AM ET
    - Change % vs their own prior futures session close
    - Labels: "DOW FUT / S&P FUT / NAS FUT"

  mode="close" (EOD report)
    - Index tickers: ^DJI / ^GSPC / ^IXIC (cash index closing levels)
    - Change % vs prior regular session close — matches CNBC end-of-day
    - Labels: "DOW / S&P 500 / NASDAQ"

Both modes: OIL=CL=F, US 10-YR=^TNX, GOLD=GC=F, SILVER=SI=F, BITCOIN=BTC-USD

Headline: always fetched fresh (no cache) — sorted by age, freshest article wins.
Price snapshot: cached 10 min per mode — delete cache/futures_cache.json to force refresh.

If futures look wrong, delete cache/futures_cache.json and rerun.
"""

import json
import logging
import os
from datetime import datetime

import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILE  = os.path.join(os.path.dirname(__file__), "..", "cache", "futures_cache.json")
CACHE_TTL_M = 10

# (premarket_label, close_label, premarket_ticker, close_ticker)
# premarket: use futures contracts directly — cash indices don't update pre-market
# close:     use cash index levels — more accurate for EOD reporting
FUTURES_TICKERS = [
    ("DOW FUT",  "DOW",      "YM=F",    "^DJI"),
    ("S&P FUT",  "S&P 500",  "ES=F",    "^GSPC"),
    ("NAS FUT",  "NASDAQ",   "NQ=F",    "^IXIC"),
    ("OIL",      "OIL",      "CL=F",    "CL=F"),
    ("US 10-YR", "US 10-YR", "^TNX",    "^TNX"),
    ("GOLD",     "GOLD",     "GC=F",    "GC=F"),
    ("SILVER",   "SILVER",   "SI=F",    "SI=F"),
    ("BITCOIN",  "BITCOIN",  "BTC-USD", "BTC-USD"),
]


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def _fetch_one(ticker: str, mode: str = "premarket") -> tuple[float | None, float | None]:
    """
    Returns (current_price, prev_regular_close).

    mode="premarket"  — use pre_market_price if available, else last_price
                        change % vs regularMarketPreviousClose (yesterday close)
    mode="close"      — use regularMarketPrice (today's official close)
                        change % vs regularMarketPreviousClose (yesterday close)
                        This is what you want for the EOD report.
    """
    try:
        t    = yf.Ticker(ticker)
        info = t.fast_info

        # Previous close: always yesterday's REGULAR session close
        prev = (
            getattr(info, "regularMarketPreviousClose", None) or
            getattr(info, "previous_close",             None)
        )

        if mode == "close":
            # Today's official closing price
            price = (
                getattr(info, "regularMarketPrice",     None) or
                getattr(info, "last_price",             None)
            )
        else:
            # Pre-market: prefer pre_market_price, fall back to last traded
            price = (
                getattr(info, "pre_market_price",       None) or
                getattr(info, "last_price",             None) or
                getattr(info, "regularMarketPrice",     None)
            )

        if price and prev:
            return float(price), float(prev)

        # Fallback: pull 2 days of daily history
        hist = t.history(period="2d", interval="1d")
        if len(hist) >= 2:
            return float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
        elif len(hist) == 1:
            return float(hist["Close"].iloc[-1]), None

    except Exception as e:
        logger.debug(f"_fetch_one({ticker}, mode={mode}): {e}")

    return None, None


def get_futures_snapshot(force: bool = False, mode: str = "premarket") -> list[dict]:
    """
    Returns list of dicts, one per indicator:
      {label, price, prev_close, chg_pct, is_up, display_price}
    Falls back to cached data if yfinance fails or cache is fresh.

    mode="premarket"  Morning report — pre-market prices vs yesterday close
    mode="close"      EOD report    — today's closing prices vs yesterday close
    """
    cache     = _load_cache()
    now       = datetime.utcnow()
    cache_key = f"data_{mode}"   # separate cache per mode

    cached_at_str = cache.get(f"_fetched_at_{mode}")
    if not force and cached_at_str:
        try:
            age_min = (now - datetime.fromisoformat(cached_at_str)).total_seconds() / 60
            if age_min < CACHE_TTL_M and cache.get(cache_key):
                return cache[cache_key]
        except Exception:
            pass

    results = []
    for premarket_label, close_label, premarket_ticker, close_ticker in FUTURES_TICKERS:
        label  = close_label if mode == "close" else premarket_label
        ticker = close_ticker if mode == "close" else premarket_ticker
        price, prev = _fetch_one(ticker, mode=mode)

        # If primary failed, try the other ticker as fallback
        fallback_ticker = premarket_ticker if mode == "close" else close_ticker
        if price is None and fallback_ticker != ticker:
            price, prev = _fetch_one(fallback_ticker, mode=mode)

        entry = {
            "label":         label,
            "price":         None,
            "prev_close":    None,
            "chg_pct":       None,
            "is_up":         None,
            "display_price": "N/A",
        }

        if price is not None:
            entry["price"]      = round(price, 4)
            entry["prev_close"] = round(prev, 4) if prev else None

            if prev and prev > 0:
                chg_pct          = (price - prev) / prev * 100
                entry["chg_pct"] = round(chg_pct, 2)
                entry["is_up"]   = chg_pct >= 0

            # Format display price
            if label == "US 10-YR":
                entry["display_price"] = f"{price:.3f}%"
            elif label == "BITCOIN":
                entry["display_price"] = f"${price:,.0f}"
            elif price >= 10_000:
                entry["display_price"] = f"${price:,.0f}"
            elif price >= 100:
                entry["display_price"] = f"${price:,.2f}"
            else:
                entry["display_price"] = f"${price:.3f}"

        results.append(entry)
        status = f"{entry['display_price']} {entry['chg_pct']:+.2f}%" if entry["chg_pct"] is not None else "N/A"
        logger.debug(f"  futures {label}: {status}")

    if any(r["price"] is not None for r in results):
        cache[f"_fetched_at_{mode}"] = now.isoformat()
        cache[cache_key]             = results
        _save_cache(cache)

    return results


def get_top_headline() -> dict:
    """
    Fetches the freshest market headline from CNBC RSS or Yahoo Finance.
    Always fetches live — no caching — so each report gets the latest article.
    Filters out articles older than 24 hours to avoid stale headlines.
    Returns: {title, url, source, age_hours} or {}
    """
    feeds = [
        ("CNBC",
         "https://search.cnbc.com/rs/search/combinedcms/view.xml"
         "?partnerId=wrss01&id=15839135"),
        ("Yahoo Finance",
         "https://finance.yahoo.com/rss/headline?s=^GSPC"),
    ]
    try:
        import requests
        from xml.etree import ElementTree as ET
        from email.utils import parsedate_to_datetime
        from datetime import timezone

        now_utc = datetime.utcnow()

        for source, url in feeds:
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible)"},
                    timeout=8,
                )
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)

                # Collect all items with timestamps, pick the freshest
                candidates = []
                for item in root.iter("item"):
                    title   = item.findtext("title", "").strip()
                    link    = item.findtext("link",  "").strip()
                    pub_str = item.findtext("pubDate", "").strip()
                    if not title or len(title) < 20:
                        continue
                    age_h = 999.0
                    if pub_str:
                        try:
                            pub_dt = parsedate_to_datetime(pub_str)
                            # Normalize to naive UTC
                            if pub_dt.tzinfo:
                                pub_dt = pub_dt.astimezone(timezone.utc).replace(tzinfo=None)
                            age_h = (now_utc - pub_dt).total_seconds() / 3600
                        except Exception:
                            pass
                    candidates.append((age_h, title, link))

                # Sort by age, take freshest
                candidates.sort(key=lambda x: x[0])
                for age_h, title, link in candidates:
                    if age_h <= 48:  # skip anything older than 48h
                        logger.debug(f"Headline ({source}, {age_h:.1f}h old): {title[:60]}")
                        return {"title": title, "url": link, "source": source, "age_hours": round(age_h, 1)}

            except Exception as e:
                logger.debug(f"headline fetch failed ({source}): {e}")
    except ImportError:
        pass
    return {}


def format_futures_text(snapshot: list[dict]) -> str:
    """One compact line per ticker for Telegram / plain text."""
    parts = []
    for f in snapshot:
        if f["price"] is None:
            continue
        if f["chg_pct"] is not None:
            arrow = "▲" if f["is_up"] else "▼"
            parts.append(f"{f['label']} {f['display_price']} {arrow}{abs(f['chg_pct']):.2f}%")
        else:
            parts.append(f"{f['label']} {f['display_price']}")
    # Two rows of 4
    mid = len(parts) // 2
    row1 = "  " + "   ".join(parts[:mid])
    row2 = "  " + "   ".join(parts[mid:])
    return row1 + "\n" + row2


def format_futures_html(snapshot: list[dict], headline: dict) -> str:
    """HTML ticker bar styled like CNBC's market data strip + headline below."""

    def cell_color(is_up):
        if is_up is None: return "#888780"
        return "#1D9E75" if is_up else "#E24B4A"

    cells = ""
    for f in snapshot:
        if f["price"] is None:
            continue
        c     = cell_color(f.get("is_up"))
        arrow = ""
        pct   = ""
        if f["chg_pct"] is not None:
            arrow = "▲" if f["is_up"] else "▼"
            pct   = f"{arrow} {abs(f['chg_pct']):.2f}%"

        cells += f"""
        <td style="padding:8px 10px;text-align:center;
                   border-right:0.5px solid #E8E6DF;white-space:nowrap">
          <div style="font-size:10px;color:#888;text-transform:uppercase;
                      letter-spacing:0.5px;margin-bottom:3px">{f['label']}</div>
          <div style="font-size:13px;font-weight:600;color:{c}">{f['display_price']}</div>
          <div style="font-size:11px;color:{c};margin-top:1px">{pct or "—"}</div>
        </td>"""

    headline_html = ""
    if headline.get("title"):
        url = headline.get("url", "#")
        src = headline.get("source", "")
        headline_html = f"""
      <div style="padding:7px 14px;background:#FAFAF8;border-top:0.5px solid #E8E6DF;
                  font-size:12px;color:#2C2C2A;line-height:1.5">
        <span style="color:#888;font-size:11px;margin-right:6px">{src}</span>
        <a href="{url}" style="color:#2C2C2A;text-decoration:none;font-weight:500"
           target="_blank">{headline['title']}</a>
      </div>"""

    return f"""
    <div style="margin-bottom:16px;border:0.5px solid #D3D1C7;border-radius:8px;
                overflow:hidden;background:#fff">
      <table style="width:100%;border-collapse:collapse">
        <tr>{cells}</tr>
      </table>
      {headline_html}
    </div>"""