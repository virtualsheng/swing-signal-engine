"""
signals/gumshoe_fetcher.py — StockGumshoe newsletter intelligence
──────────────────────────────────────────────────────────────────
Fetches the last 3 days of StockGumshoe articles via RSS, reads
each article's full text, and uses AI to produce a rich summary:
  - Core investment thesis
  - Specific tickers with buy/avoid/watch recommendation
  - Position sizing relative to your portfolio
  - Risk level and catalyst
  - Cross-reference against your current holdings and EOD signals

StockGumshoe "sleuths" paid newsletter teaser stocks — identifying
the actual company behind vague promotions. Useful as a
contrarian/alternative signal layer alongside your technical signals.

Note: StockGumshoe uses Cloudflare — article fetch works from home
  residential IPs but is blocked from cloud/server IPs. RSS works fine.
  When article text is unavailable, AI analyses the RSS description instead.

RSS:   https://www.stockgumshoe.com/feed/
Used by: run_morning.py
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_router import llm_call, llm_call_text

logger = logging.getLogger(__name__)

CACHE_FILE      = os.path.join(os.path.dirname(__file__), "..", "cache", "gumshoe_cache.json")
MAX_AGE_H       = 20     # re-fetch after 20 hours
MAX_ARTICLES    = 3      # last N articles to analyse
FETCH_TIMEOUT   = 12

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

KNOWN_TICKERS = {
    "SPY","QQQ","AAPL","MSFT","AMZN","GOOGL","META","NVDA","TSLA","AMD",
    "INTC","TSM","AVGO","MU","LRCX","AMAT","PLTR","JPM","GLD","GLDM",
    "GDE","PSLV","IBIT","DBC","DBMF","VUG","AVUV","RKLB","ARIS","GEV",
    "PAAS","AG","SNDK","REMX","EWT","EWY","EWJV","GRID","NANR","SPMO",
    "UFO","URA","DRAM","QQQM","VLUE","XLY","XLK","XLF","XLE","TLT","IWM",
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── RSS fetch ─────────────────────────────────────────────────────────────────

def _fetch_rss_articles(max_articles: int = MAX_ARTICLES) -> list[dict]:
    """
    Fetch latest article metadata from StockGumshoe RSS.
    Returns list of article dicts with title, url, pub_date, age_hours, description.
    """
    feeds = [
        "https://www.stockgumshoe.com/feed/",
        "https://www.stockgumshoe.com/feed/atom/",
    ]
    for feed_url in feeds:
        try:
            resp = requests.get(
                feed_url,
                headers={**BROWSER_HEADERS,
                         "Accept": "application/rss+xml, application/xml, text/xml"},
                timeout=FETCH_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.debug(f"RSS {feed_url}: HTTP {resp.status_code}")
                continue

            root  = ET.fromstring(resp.content)
            items = root.findall(".//item")
            if not items:
                continue

            now_utc  = datetime.now(timezone.utc)
            articles = []
            for item in items[:max_articles * 2]:   # fetch extra, filter by age
                title    = item.findtext("title",  "").strip()
                url      = item.findtext("link",   "").strip()
                pub_str  = item.findtext("pubDate","").strip()
                desc_raw = item.findtext("description", "").strip()
                # Strip HTML from description
                desc = re.sub(r"<[^>]+>", " ", desc_raw)
                desc = re.sub(r"\s+",     " ", desc).strip()[:1200]

                # Parse age
                age_hours = 999.0
                if pub_str:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub_str)
                        age_hours = (now_utc - pub_dt).total_seconds() / 3600
                    except Exception:
                        pass

                if not title or not url:
                    continue

                articles.append({
                    "title":     title,
                    "url":       url,
                    "pub_date":  pub_str[:16] if pub_str else "",
                    "age_hours": round(age_hours, 1),
                    "rss_desc":  desc,
                })

            # Filter to last 3 days (72 hours)
            fresh = [a for a in articles if a["age_hours"] <= 72]
            if fresh:
                logger.info(f"StockGumshoe RSS: {len(fresh)} articles within 72h")
                return fresh[:max_articles]

            # If nothing within 72h, return the most recent as fallback
            if articles:
                logger.info(f"StockGumshoe RSS: no articles within 72h, using most recent")
                return articles[:1]

        except Exception as e:
            logger.debug(f"RSS fetch failed ({feed_url}): {e}")

    return []


# ── Article text fetch ────────────────────────────────────────────────────────

def _fetch_article_text(url: str) -> str:
    """
    Fetch full article text from URL.
    Works from residential IPs; Cloudflare blocks cloud/server IPs (403).
    Falls back gracefully — RSS description used instead.
    Returns cleaned plain text, up to 6000 chars.
    """
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            logger.debug(f"Article HTTP {resp.status_code}: {url[-60:]}")
            return ""

        html = resp.text

        # Remove boilerplate
        for pat in [
            r"<script[^>]*>.*?</script>",
            r"<style[^>]*>.*?</style>",
            r"<nav[^>]*>.*?</nav>",
            r"<footer[^>]*>.*?</footer>",
            r"<header[^>]*>.*?</header>",
            r"<!--.*?-->",
        ]:
            html = re.sub(pat, " ", html, flags=re.DOTALL)

        # Extract content from article body div
        for content_class in [
            "entry-content", "post-content", "article-body",
            "article-content", "the-content", "content-area"
        ]:
            m = re.search(
                rf'class="[^"]*{content_class}[^"]*"[^>]*>(.*?)</(?:div|article)',
                html, re.DOTALL | re.IGNORECASE
            )
            if m:
                html = m.group(1)
                break

        # Strip remaining HTML
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+",     " ", text).strip()

        if len(text) < 100:
            return ""

        # Return up to 6000 chars (enough for solid AI analysis)
        return text[:6000]

    except Exception as e:
        logger.debug(f"Article fetch failed ({url[-50:]}): {e}")
        return ""


# ── Ticker extraction ─────────────────────────────────────────────────────────

def _extract_tickers(text: str, portfolio_symbols: list[str]) -> list[str]:
    """Find all tickers mentioned in text."""
    all_tickers = KNOWN_TICKERS | set(t.upper() for t in portfolio_symbols)
    found = set()
    # Standard ticker lookup
    for ticker in all_tickers:
        if re.search(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE):
            found.add(ticker.upper())
    # $TICKER pattern
    for t in re.findall(r"\$([A-Z]{2,5})\b", text):
        if len(t) <= 5:
            found.add(t.upper())
    return sorted(found)


# ── AI analysis ───────────────────────────────────────────────────────────────

def _analyze_article(
    title:             str,
    rss_desc:          str,
    full_text:         str,
    portfolio_symbols: list[str],
    portfolio_value:   float,
    signal_log:        dict,
) -> dict:
    """
    Deep AI analysis of a StockGumshoe article.
    Uses full article text when available, RSS description as fallback.
    """
    # Choose best available content — full text preferred
    content = full_text if len(full_text) > len(rss_desc) else rss_desc
    # Use up to 4500 chars — enough for thorough analysis without hitting token limits
    content_trimmed = content[:4500]

    # Pre-extract tickers via regex (fast, AI will refine)
    regex_tickers = _extract_tickers(content, portfolio_symbols)
    held_tickers  = [t for t in regex_tickers if t in set(t.upper() for t in portfolio_symbols)]

    portfolio_k   = portfolio_value / 1000
    port_syms_str = ", ".join(portfolio_symbols[:20]) if portfolio_symbols else "none"

    prompt = f"""You are analyzing a StockGumshoe article for a retirement investor.
StockGumshoe identifies stocks teased in paid newsletters and evaluates the investment thesis.

Article title: {title}

Full article content:
---
{content_trimmed}
---

Investor's current holdings: {port_syms_str}
Portfolio value: ~${portfolio_k:.0f}k (retirement accounts — moderate risk tolerance)

Provide a comprehensive analysis. Respond ONLY with valid JSON, no markdown:
{{
  "main_ticker": "<primary stock ticker, or null if none>",
  "tickers_mentioned": ["<TICKER1>", "<TICKER2>"],
  "recommendation": "<buy|avoid|watch|neutral>",
  "thesis_summary": "<3-4 sentences: what is the newsletter claiming, what does StockGumshoe think, and what is the actual company/sector>",
  "detailed_analysis": "<4-5 sentences: expand on the investment thesis, key risks, why the newsletter is promoting this, historical context if available, and whether the fundamentals support the thesis>",
  "risk_level": "<speculative|moderate|conservative>",
  "risk_factors": ["<risk1>", "<risk2>"],
  "time_horizon": "<days|weeks|months|years>",
  "price_target": <float or null>,
  "catalyst": "<specific catalyst mentioned, or null>",
  "sector": "<sector/industry>",
  "in_your_portfolio": ["<tickers you already hold>"],
  "position_suggestion": "<2 sentences: specific sizing advice referencing the ${portfolio_k:.0f}k portfolio, e.g. limit to 1-2% ($x-$x) given speculative nature, or avoid entirely>",
  "newsletter_bias": "<bullish|bearish|neutral>",
  "gumshoe_verdict": "<2 sentences: StockGumshoe's actual conclusion — is this legitimate or promotional hype?>",
  "action_items": ["<concrete action 1>", "<concrete action 2>"]
}}

Rules:
- thesis_summary and detailed_analysis MUST be specific to this article
- For speculative small-caps: recommend no more than 1-2% of portfolio
- For established names: normal position sizing applies
- gumshoe_verdict should reflect what StockGumshoe's editorial stance actually is
- If the article is a "microblog" or teaser, note that explicitly"""

    result = llm_call(prompt, expect_json=True, timeout=45, tag="gumshoe/analyze")

    if result and result.get("thesis_summary") and len(result["thesis_summary"]) > 30:
        # Clean and validate
        result["tickers_mentioned"] = [
            t.upper() for t in result.get("tickers_mentioned", [])
            if isinstance(t, str) and 1 <= len(t.strip()) <= 6
        ]
        # Add any regex-found tickers AI missed
        for t in regex_tickers:
            if t not in result["tickers_mentioned"]:
                result["tickers_mentioned"].append(t)
        result["in_your_portfolio"] = [
            t.upper() for t in result.get("in_your_portfolio", held_tickers)
            if isinstance(t, str)
        ]
        result["signal_alignment"] = _cross_reference(
            result["tickers_mentioned"], signal_log
        )
        logger.info(f"  AI analysis complete: {result.get('main_ticker','?')} | "
                    f"{result.get('recommendation','?')} | {result.get('risk_level','?')}")
        return result

    # Fallback: structured data from regex + descriptive text
    logger.warning("  AI analysis failed — using regex fallback")
    return {
        "main_ticker":        regex_tickers[0] if regex_tickers else None,
        "tickers_mentioned":  regex_tickers[:5],
        "recommendation":     "watch",
        "thesis_summary":     rss_desc[:300] if rss_desc else title,
        "detailed_analysis":  "AI analysis unavailable. Read the full article for details.",
        "risk_level":         "moderate",
        "risk_factors":       ["Unknown — AI unavailable"],
        "time_horizon":       "unknown",
        "price_target":       None,
        "catalyst":           None,
        "sector":             "unknown",
        "in_your_portfolio":  held_tickers,
        "position_suggestion":"Review manually — AI analysis unavailable.",
        "newsletter_bias":    "neutral",
        "gumshoe_verdict":    "Review the full article at the link above.",
        "action_items":       ["Read full article before acting"],
        "signal_alignment":   _cross_reference(regex_tickers, signal_log),
    }


def _cross_reference(tickers: list[str], signal_log: dict) -> list[dict]:
    results = []
    for ticker in tickers:
        for key, val in signal_log.items():
            if key.endswith(f":{ticker}"):
                sig = val.get("signal", "HOLD")
                cv  = val.get("conviction", 50)
                if sig != "HOLD" or val.get("held", False):
                    results.append({
                        "symbol":     ticker,
                        "eod_signal": sig,
                        "conviction": cv,
                        "account":    key.split(":")[0],
                        "held":       val.get("held", False),
                    })
    return results


# ── Main entry ────────────────────────────────────────────────────────────────

def fetch_gumshoe_analysis(
    portfolio_symbols: list[str],
    signal_log:        dict,
    portfolio_value:   float = 750_000,
    force:             bool  = False,
) -> list[dict]:
    """
    Fetch and analyse the last 3 days of StockGumshoe articles.
    Returns list of analysis dicts (one per article), newest first.
    """
    cache = _load_cache()

    articles = _fetch_rss_articles(max_articles=MAX_ARTICLES)

    if not articles:
        # Cache fallback
        if cache.get("analyses"):
            logger.info("StockGumshoe: RSS failed — returning cached analyses")
            for a in cache["analyses"]:
                a["_stale_cache"] = True
            return cache["analyses"]
        logger.warning("StockGumshoe: no articles and no cache")
        return []

    # Check if all cached articles are still current
    cached_urls = {a.get("url") for a in cache.get("analyses", [])}
    fresh_urls  = {a["url"] for a in articles}

    if not force and cached_urls == fresh_urls and cache.get("analyzed_at"):
        try:
            cached_at = datetime.fromisoformat(cache["analyzed_at"])
            age_h = (datetime.utcnow() - cached_at).total_seconds() / 3600
            if age_h < MAX_AGE_H:
                logger.info(f"StockGumshoe: cache hit ({len(articles)} articles, {age_h:.1f}h old)")
                # Refresh signal alignment (changes daily)
                for a in cache["analyses"]:
                    a["signal_alignment"] = _cross_reference(
                        a.get("tickers_mentioned", []), signal_log
                    )
                return cache["analyses"]
        except Exception:
            pass

    # Analyse each article
    analyses = []
    for i, article in enumerate(articles):
        logger.info(
            f"StockGumshoe [{i+1}/{len(articles)}]: "
            f"'{article['title'][:55]}' ({article['age_hours']:.1f}h old)"
        )

        # Fetch full article text
        full_text = _fetch_article_text(article["url"])
        word_count = len(full_text.split()) if full_text else 0
        if word_count > 50:
            logger.info(f"  Article text: {word_count} words")
        else:
            logger.info(f"  Article text unavailable (Cloudflare) — using RSS description")

        analysis = _analyze_article(
            title             = article["title"],
            rss_desc          = article["rss_desc"],
            full_text         = full_text,
            portfolio_symbols = portfolio_symbols,
            portfolio_value   = portfolio_value,
            signal_log        = signal_log,
        )
        analysis.update({
            "title":     article["title"],
            "url":       article["url"],
            "pub_date":  article["pub_date"],
            "age_hours": article["age_hours"],
            "source":    "StockGumshoe",
        })
        analyses.append(analysis)

        # Small delay between articles to avoid rate limits
        if i < len(articles) - 1:
            time.sleep(1)

    # Save to cache (without signal_alignment — recomputed fresh each run)
    cache_safe = []
    for a in analyses:
        c = {k: v for k, v in a.items() if k != "signal_alignment"}
        cache_safe.append(c)

    cache["analyses"]    = cache_safe
    cache["analyzed_at"] = datetime.utcnow().isoformat()
    _save_cache(cache)

    return analyses


# ── HTML renderer ─────────────────────────────────────────────────────────────

def format_gumshoe_html(analyses: list[dict]) -> str:
    """Render StockGumshoe analyses as HTML sections for the morning report."""
    if not analyses:
        return ""

    sections = []
    for analysis in analyses:
        ticker      = analysis.get("main_ticker") or "?"
        title       = analysis.get("title", "")
        url         = analysis.get("url", "#")
        rec         = analysis.get("recommendation", "watch").lower()
        risk        = analysis.get("risk_level", "moderate")
        thesis      = analysis.get("thesis_summary", "")
        detailed    = analysis.get("detailed_analysis", "")
        verdict     = analysis.get("gumshoe_verdict", "")
        position    = analysis.get("position_suggestion", "")
        catalyst    = analysis.get("catalyst") or ""
        target      = analysis.get("price_target")
        sector      = analysis.get("sector", "")
        tickers     = analysis.get("tickers_mentioned", [])
        in_port     = analysis.get("in_your_portfolio", [])
        aligned     = analysis.get("signal_alignment", [])
        risk_factors= analysis.get("risk_factors", [])
        action_items= analysis.get("action_items", [])
        age         = analysis.get("age_hours", 0)
        stale       = analysis.get("_stale_cache", False)
        bias        = analysis.get("newsletter_bias", "neutral")

        rec_color  = {"buy":"#1D9E75","avoid":"#E24B4A","watch":"#BA7517","neutral":"#888"}.get(rec,"#888")
        risk_color = {"speculative":"#E24B4A","moderate":"#BA7517","conservative":"#1D9E75"}.get(risk,"#888")
        bias_color = {"bullish":"#1D9E75","bearish":"#E24B4A","neutral":"#888"}.get(bias,"#888")

        stale_badge = (
            '<span style="background:#FAC775;color:#633806;font-size:10px;'
            'padding:2px 6px;border-radius:4px;margin-left:8px">'
            '⚠️ Cached</span>'
        ) if stale else ""

        # Tickers row with portfolio highlighting
        ticker_pills = "".join(
            f'<span style="display:inline-block;margin:2px 3px;padding:2px 8px;'
            f'background:{"#E1F5EE" if t in in_port else "#F1EFE8"};'
            f'border-radius:4px;font-size:12px;'
            f'color:{"#0F6E56" if t in in_port else "#5F5E5A"};'
            f'{"font-weight:500;" if t in in_port else ""}">'
            f'{t}{"✓" if t in in_port else ""}</span>'
            for t in tickers[:10]
        )

        # Signal alignment
        align_html = ""
        if aligned:
            rows = "".join(
                f'<span style="display:inline-block;margin:2px 4px;padding:2px 8px;'
                f'border-radius:4px;font-size:11px;background:#F1EFE8;color:#2C2C2A">'
                f'{a["symbol"]} {a["eod_signal"]} cv={a["conviction"]} [{a["account"]}]</span>'
                for a in aligned
            )
            align_html = f'<div style="margin-top:8px">{rows}</div>'

        # Risk factors
        risk_html = ""
        if risk_factors:
            rf = " · ".join(risk_factors[:3])
            risk_html = f'<div style="font-size:12px;color:#E24B4A;margin-top:4px">⚠️ {rf}</div>'

        # Action items
        action_html = ""
        if action_items:
            items_html = "".join(
                f'<li style="margin:2px 0">{item}</li>'
                for item in action_items[:3]
            )
            action_html = (
                f'<ul style="margin:6px 0 0;padding-left:18px;'
                f'font-size:12px;color:#2C2C2A">{items_html}</ul>'
            )

        catalyst_html = ""
        if catalyst and catalyst.lower() not in ("null","none",""):
            catalyst_html = (
                f'<div style="margin-top:4px;font-size:12px;color:#5F5E5A">'
                f'<strong>Catalyst:</strong> {catalyst}</div>'
            )

        target_html = (
            f'&nbsp;<span style="color:#1D9E75;font-size:12px">target ${target:.2f}</span>'
        ) if target else ""

        sector_html = (
            f'<span style="color:#888;font-size:11px;margin-left:8px">{sector}</span>'
        ) if sector and sector != "unknown" else ""

        sections.append(f"""
    <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
      <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7;
                  display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-size:18px">🔍</span>
        <div style="flex:1">
          <span style="font-weight:500;font-size:14px">StockGumshoe</span>
          <span style="color:#888;font-size:12px;margin-left:8px">{age:.0f}h ago</span>
          {stale_badge}
        </div>
        <a href="{url}" target="_blank" style="font-size:12px;color:#4ca3ff;text-decoration:none">Read ↗</a>
      </div>
      <div style="padding:12px 14px">

        <a href="{url}" target="_blank"
           style="font-size:14px;font-weight:500;color:#2C2C2A;text-decoration:none;
                  display:block;margin-bottom:10px;line-height:1.4">
          {title}
        </a>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center">
          <span style="padding:3px 10px;border-radius:4px;font-size:12px;font-weight:600;
                       color:#fff;background:{rec_color}">{rec.upper()}</span>
          <span style="padding:3px 10px;border-radius:4px;font-size:12px;
                       color:{risk_color};border:0.5px solid {risk_color}">{risk}</span>
          <span style="padding:3px 10px;border-radius:4px;font-size:12px;
                       color:{bias_color};border:0.5px solid {bias_color}">{bias}</span>
          {target_html}{sector_html}
        </div>

        <div style="font-size:13px;color:#2C2C2A;line-height:1.65;margin-bottom:8px">
          {thesis}
        </div>

        <div style="font-size:12px;color:#5F5E5A;line-height:1.6;margin-bottom:8px;
                    padding:8px 12px;background:#FAFAF8;border-radius:6px;border-left:3px solid #D3D1C7">
          {detailed}
        </div>

        {catalyst_html}
        {risk_html}

        <div style="margin-top:10px;padding:8px 12px;background:#F7F5EE;
                    border-radius:6px;font-size:12px;color:#2C2C2A">
          <strong>📊 Position sizing:</strong> {position}
        </div>

        <div style="margin-top:8px;font-size:12px;color:#5F5E5A;font-style:italic;
                    padding:6px 10px;border-left:2px solid #1D9E75;background:#FAFAF8">
          <strong>StockGumshoe verdict:</strong> {verdict}
        </div>

        {f'<div style="margin-top:8px;font-size:12px;font-weight:500;color:#5F5E5A">Action items:</div>{action_html}' if action_items else ''}

        {f'<div style="margin-top:8px;font-size:12px;font-weight:500;color:#0F6E56">⚡ Signal alignment:</div>{align_html}' if aligned else ''}

        <div style="margin-top:10px;font-size:12px;color:#5F5E5A">
          <strong>Tickers:</strong> {ticker_pills or '—'}
        </div>

      </div>
    </div>""")

    return "\n".join(sections)


def format_gumshoe_text(analyses: list[dict]) -> str:
    """Plain text for Telegram/email fallback."""
    if not analyses:
        return ""
    parts = []
    for a in analyses:
        rec     = a.get("recommendation","watch").upper()
        risk    = a.get("risk_level","moderate")
        title   = a.get("title","?")
        thesis  = a.get("thesis_summary","")
        pos     = a.get("position_suggestion","")
        tickers = ", ".join(a.get("tickers_mentioned",[])[:5])
        url     = a.get("url","")
        parts.append(
            f"🔍 STOCKGUMSHOE — {rec} ({risk} risk)\n"
            f"  {title[:80]}\n"
            f"  {thesis[:150]}\n"
            f"  Sizing: {pos[:100]}\n"
            f"  Tickers: {tickers}\n"
            f"  {url}"
        )
    return "\n\n".join(parts)