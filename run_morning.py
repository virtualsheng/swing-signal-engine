"""
run_morning.py — Morning Intelligence Report (7:30 AM ET)
──────────────────────────────────────────────────────────
Overnight context before you execute anything. Reads last night's
EOD signals and enriches them with:
  - CNBC-style futures bar: DOW FUT, S&P FUT, NAS FUT, OIL, US 10-YR,
    GOLD, SILVER, BITCOIN + top market headline
  - Pre-market price moves for all holdings
  - Gap analysis (which positions gapped overnight)
  - News sentiment per symbol (Yahoo Finance + Ollama)
  - Market overview: SPY/QQQ futures direction, VIX level
  - Options expected move for SPY and QQQ
  - YouTube channel analysis
  - Earnings calendar: any of your symbols reporting this week
  - Watchlist for today: which EOD signals survived overnight

This is READING MATERIAL, not a trade list.
The 9:50 AM opening report (run_opening.py) tells you what to execute.

Schedule: 7:30 AM ET daily (Mon–Fri)
  python run_morning.py
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

_log_file = f"logs/morning_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

from signals.premarket_data  import get_premarket_batch, get_market_overview, gap_significance
from signals.expected_move   import get_market_expected_moves, format_em_html, format_em_text
from signals.youtube_fetcher import fetch_all_channels
from signals.news_fetcher    import fetch_news_batch, sentiment_emoji
from signals.earnings_filter import is_near_earnings
from signals.portfolio       import load_portfolio, get_tradeable_accounts
from signals.ai_engine       import check_ollama_available
from signals.market_futures        import (
    get_futures_snapshot, get_top_headline,
    format_futures_text, format_futures_html,
)
from signals.auto_update_portfolio import auto_update as auto_update_portfolio
from notifications.notifier        import deliver_report

SIGNAL_LOG_FILE = "cache/signal_log.json"


def load_signal_log() -> dict:
    try:
        with open(SIGNAL_LOG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _ollama_morning_narrative(
    ollama_ok: bool,
    market_overview: dict,
    active_signals: list,
    gap_alerts: list,
    news_highlights: list,
) -> str:
    """Generate AI morning narrative using Ollama."""
    if not ollama_ok:
        spy_gap = market_overview.get("spy", {}).get("gap_pct", 0)
        qqq_gap = market_overview.get("qqq", {}).get("gap_pct", 0)
        vix     = market_overview.get("vix", {}).get("price", "N/A")
        return (
            f"Pre-market: SPY {spy_gap:+.1f}%, QQQ {qqq_gap:+.1f}%, VIX {vix}. "
            f"{len(active_signals)} EOD signal(s) active from last night. "
            f"Review gap alerts and news before the open."
        )

    import requests
    spy_gap  = market_overview.get("spy", {}).get("gap_pct", 0)
    qqq_gap  = market_overview.get("qqq", {}).get("gap_pct", 0)
    vix      = market_overview.get("vix", {}).get("price", "N/A")
    vix_lvl  = market_overview.get("vix", {}).get("level", "normal")

    signal_lines = "\n".join(
        f"  {s['symbol']:6} {s['eod_signal']:5} cv={s['conviction']:3d} "
        f"(pre-mkt: {s.get('gap_pct',0):+.1f}%)"
        for s in active_signals[:6]
    )
    gap_lines = "\n".join(
        f"  {g['symbol']:6} {g['gap_pct']:+.1f}%  {g['gap_label']:12} [{g['account']}]"
        for g in gap_alerts[:5]
    ) or "  No significant gaps."
    news_lines = "\n".join(
        f"  {n['symbol']:6} {n['emoji']} {n['label']:14} — {n['summary'][:60]}"
        for n in news_highlights[:5]
    ) or "  No significant news."

    prompt = f"""You are a pre-market analyst writing a morning briefing for a retirement portfolio investor.

Market overview (pre-market, 7:30 AM ET):
  SPY: {spy_gap:+.2f}%  |  QQQ: {qqq_gap:+.2f}%  |  VIX: {vix} ({vix_lvl})

Active EOD signals from last night (to be confirmed at open):
{signal_lines or "  None"}

Pre-market moves in your holdings (genuine pre-market quotes only):
{gap_lines}

Key news sentiment:
{news_lines}

Write a 4–5 sentence pre-market briefing. Cover:
1. Overall market tone from futures
2. What the gaps/news mean for today's potential trades
3. What to watch at the open (9:30–9:45 AM)
4. Any risks or reasons to be cautious today

Keep it practical and specific. No bullet points. Plain prose. Professional tone."""

    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen3:8b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            if len(text) > 50:
                return text
    except Exception as e:
        logger.debug(f"Morning narrative Ollama call failed: {e}")

    return (
        f"Pre-market shows SPY {spy_gap:+.1f}%, QQQ {qqq_gap:+.1f}%, "
        f"VIX at {vix} ({vix_lvl}). "
        f"{len(active_signals)} signal(s) from last night remain active. "
        f"Review gap alerts below before the open and watch for confirmation "
        f"in the first 15 minutes of trading."
    )


def build_morning_html(
    today_str: str,
    market_overview: dict,
    active_signals: list,
    gap_alerts: list,
    news_data: dict,
    earnings_alerts: list,
    morning_narrative: str,
    total_value: float,
    yt_analyses: list = None,
    em_data: dict = None,
    futures_snap: list = None,
    top_headline: dict = None,
) -> str:
    spy_gap = market_overview.get("spy", {}).get("gap_pct", 0)
    qqq_gap = market_overview.get("qqq", {}).get("gap_pct", 0)
    vix     = market_overview.get("vix", {}).get("price", "N/A")
    vix_lvl = market_overview.get("vix", {}).get("level", "normal")
    ts      = market_overview.get("timestamp", "")

    def chg_c(v): return "#1D9E75" if v >= 0 else "#E24B4A"
    def chg_str(v): return f'<span style="color:{chg_c(v)};font-weight:500">{v:+.2f}%</span>'

    vix_color = {"extreme fear":"#E24B4A","elevated":"#BA7517",
                 "normal":"#888780","complacent":"#1D9E75"}.get(vix_lvl, "#888780")

    # ── CNBC-style futures ticker bar + headline ───────────────────────────
    futures_html = ""
    if futures_snap:
        futures_html = format_futures_html(futures_snap, top_headline or {})

    # ── Market overview bar (SPY / QQQ / VIX) ─────────────────────────────
    overview_html = f"""
    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:20px;padding:14px;background:#F1EFE8;border-radius:8px">
      <div>
        <div style="font-size:12px;color:#5F5E5A">SPY pre-mkt</div>
        <div style="font-size:18px;font-weight:500">{chg_str(spy_gap)}</div>
      </div>
      <div>
        <div style="font-size:12px;color:#5F5E5A">QQQ pre-mkt</div>
        <div style="font-size:18px;font-weight:500">{chg_str(qqq_gap)}</div>
      </div>
      <div>
        <div style="font-size:12px;color:#5F5E5A">VIX</div>
        <div style="font-size:18px;font-weight:500;color:{vix_color}">{vix} <span style="font-size:12px;font-weight:400">({vix_lvl})</span></div>
      </div>
      <div style="margin-left:auto;font-size:12px;color:#888;align-self:center">{ts}</div>
    </div>"""

    # ── AI narrative ───────────────────────────────────────────────────────
    narrative_html = f"""
    <div style="margin-bottom:20px;padding:14px 16px;border-left:3px solid #1D9E75;background:#FAFAF8;border-radius:0 6px 6px 0">
      <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px">Morning Briefing</div>
      <p style="margin:0;font-size:13px;line-height:1.7;color:#2C2C2A">{morning_narrative}</p>
    </div>"""

    # ── Gap alerts ─────────────────────────────────────────────────────────
    gap_rows = ""
    for g in sorted(gap_alerts, key=lambda x: -abs(x["gap_pct"])):
        sig_label = gap_significance(g["gap_pct"], g.get("asset_class","etf"))
        sig_color = {"major":"#E24B4A","significant":"#BA7517","moderate":"#5F5E5A","minor":"#888"}.get(sig_label,"#888")
        eod_sig   = g.get("eod_signal","HOLD")
        eod_color = {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75","SELL":"#E24B4A","STRONG_SELL":"#E24B4A"}.get(eod_sig,"#888")
        impact    = ""
        if eod_sig in ("BUY","STRONG_BUY") and g["gap_pct"] < -1.5:
            impact = '<span style="color:#E24B4A;font-size:12px"> ⚠️ may invalidate BUY signal</span>'
        elif eod_sig in ("SELL","STRONG_SELL") and g["gap_pct"] > 1.5:
            impact = '<span style="color:#E24B4A;font-size:12px"> ⚠️ may invalidate SELL signal</span>'
        elif eod_sig in ("BUY","STRONG_BUY") and g["gap_pct"] > 0.5:
            impact = '<span style="color:#1D9E75;font-size:12px"> ✓ strengthens BUY signal</span>'

        gap_rows += f"""
        <tr style="border-top:0.5px solid #E8E6DF">
          <td style="padding:7px 8px;font-weight:500">{g['symbol']}</td>
          <td style="padding:7px 8px">{chg_str(g['gap_pct'])}</td>
          <td style="padding:7px 8px;color:{sig_color};font-size:12px">{sig_label}</td>
          <td style="padding:7px 8px;color:{eod_color};font-size:12px">{eod_sig}</td>
          <td style="padding:7px 8px;font-size:12px;color:#5F5E5A">{g['account']}{impact}</td>
        </tr>"""

    gaps_section = f"""
    <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
      <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
        <span style="font-weight:500" id="gap-section-title">Pre-Market Moves — Your Holdings</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#F7F5EE">
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Gap</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Significance</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">EOD Signal</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Account / Impact</th>
        </tr></thead>
        <tbody>{gap_rows or "<tr><td colspan='5' style='padding:10px 8px;color:#888;font-size:13px'>No pre-market data available — market may be closed or pre-market not yet active.</td></tr>"}</tbody>
      </table>
    </div>"""

    # ── Active signals watchlist ───────────────────────────────────────────
    signal_rows = ""
    for s in sorted(active_signals, key=lambda x: -x["conviction"]):
        sig   = s["eod_signal"]
        color = {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75","SELL":"#E24B4A","STRONG_SELL":"#E24B4A"}.get(sig,"#888")
        pm    = s.get("gap_pct", 0)
        size  = s.get("suggested_usd", 0)
        news  = news_data.get(s["symbol"], {})
        sent  = news.get("sentiment", 0)
        sent_e= sentiment_emoji(sent)
        watch_note = ""
        if sig in ("BUY","STRONG_BUY"):
            watch_note = "Watch for confirmation above OR high at open"
        elif sig in ("SELL","STRONG_SELL"):
            watch_note = "Watch for continuation lower — consider reducing"
        signal_rows += f"""
        <tr style="border-top:0.5px solid #E8E6DF">
          <td style="padding:7px 8px;font-weight:500">{s['symbol']}</td>
          <td style="padding:7px 8px;color:{color};font-weight:500">{sig}</td>
          <td style="padding:7px 8px">{s['conviction']}/100</td>
          <td style="padding:7px 8px;color:{"#1D9E75" if pm>=0 else "#E24B4A"}">{pm:+.1f}%</td>
          <td style="padding:7px 8px">{sent_e} {news.get('sentiment_label','')}</td>
          <td style="padding:7px 8px;font-size:12px;color:#5F5E5A">${size:,.0f} | {s['account']}</td>
          <td style="padding:7px 8px;font-size:12px;color:#5F5E5A;font-style:italic">{watch_note}</td>
        </tr>"""

    watchlist_section = f"""
    <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
      <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
        <span style="font-weight:500">Today's Watchlist</span>
        <span style="color:#888;font-size:12px;margin-left:8px">EOD signals from last night — confirmation at 9:50 AM</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#F7F5EE">
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Signal</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Conviction</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Pre-mkt</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">News</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Size / Account</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Watch for</th>
        </tr></thead>
        <tbody>{signal_rows or "<tr><td colspan='7' style='padding:10px 8px;color:#888;font-size:13px'>No active signals from last night.</td></tr>"}</tbody>
      </table>
    </div>"""

    # ── News sentiment ─────────────────────────────────────────────────────
    news_rows = ""
    for sym, nd in sorted(news_data.items(), key=lambda x: -abs(x[1].get("sentiment",0))):
        if not nd.get("headlines"):
            continue
        emoji    = sentiment_emoji(nd.get("sentiment", 0))
        label    = nd.get("sentiment_label", "neutral")
        summary  = nd.get("sentiment_summary", "")[:80]
        catalyst = nd.get("catalyst", "none")
        cat_html = f'<span style="color:#BA7517;font-size:11px"> [{catalyst}]</span>' if catalyst and catalyst != "none" else ""
        recent   = nd.get("headlines", [{}])[0].get("title", "")[:70]
        news_rows += f"""
        <tr style="border-top:0.5px solid #E8E6DF">
          <td style="padding:7px 8px;font-weight:500">{sym}</td>
          <td style="padding:7px 8px">{emoji} {label}{cat_html}</td>
          <td style="padding:7px 8px;font-size:12px;color:#5F5E5A">{summary}</td>
          <td style="padding:7px 8px;font-size:11px;color:#888;font-style:italic">{recent}</td>
        </tr>"""

    news_section = f"""
    <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
      <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
        <span style="font-weight:500">News Sentiment</span>
        <span style="color:#888;font-size:12px;margin-left:8px">AI-graded from Yahoo Finance headlines</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#F7F5EE">
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Sentiment</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Summary</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Latest headline</th>
        </tr></thead>
        <tbody>{news_rows or "<tr><td colspan='4' style='padding:10px 8px;color:#888;font-size:13px'>No news data available.</td></tr>"}</tbody>
      </table>
    </div>"""

    # ── Earnings alerts ────────────────────────────────────────────────────
    earn_html = ""
    if earnings_alerts:
        earn_rows = "".join(
            f'<tr style="border-top:0.5px solid #E8E6DF">'
            f'<td style="padding:7px 8px;font-weight:500;color:#BA7517">{e["symbol"]}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#5F5E5A">{e["account"]}</td>'
            f'<td style="padding:7px 8px;font-size:12px;color:#E24B4A">{e["note"]}</td>'
            f'</tr>'
            for e in earnings_alerts
        )
        earn_html = f"""
        <div style="margin-bottom:20px;border:0.5px solid #F5D38A;border-radius:8px;overflow:hidden">
          <div style="background:#FDF8EC;padding:10px 14px;border-bottom:0.5px solid #F5D38A">
            <span style="font-weight:500;color:#BA7517">⚠️ Earnings Alerts</span>
            <span style="color:#888;font-size:12px;margin-left:8px">BUY signals blocked within 48h of earnings</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tbody>{earn_rows}</tbody>
          </table>
        </div>"""

    # ── YouTube analysis sections ──────────────────────────────────────────
    yt_sections_html = ""
    for yt_analysis in (yt_analyses or []):
        if yt_analysis.get("error"):
            continue
        bias       = yt_analysis.get("overall_bias", "neutral")
        bias_color = {"bullish":"#1D9E75","bearish":"#E24B4A","neutral":"#888780",
                      "cautious":"#BA7517","mixed":"#888780"}.get(bias.lower(),"#888780")

        # Price targets table
        pt_table = ""
        pts = yt_analysis.get("price_targets", [])
        if pts:
            pt_rows = "".join(
                f'<tr style="border-top:0.5px solid #E8E6DF">'
                f'<td style="padding:5px 8px;font-weight:500">{pt.get("symbol","")}</td>'
                f'<td style="padding:5px 8px">{pt.get("direction","")}</td>'
                f'<td style="padding:5px 8px">{pt.get("target","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px;color:#5F5E5A">{pt.get("comment","")}</td>'
                f'</tr>'
                for pt in pts
            )
            pt_table = f"""
            <div style="margin-top:10px">
              <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:4px">Price Targets Mentioned</div>
              <table style="width:100%;border-collapse:collapse;font-size:13px;background:#F7F5EE;border-radius:6px">
                <tbody>{pt_rows}</tbody>
              </table>
            </div>"""

        # Cross-reference table
        cr_rows = ""
        for cr in yt_analysis.get("portfolio_crossref", []):
            align_color = {"aligned":"#1D9E75","opposed":"#E24B4A","neutral":"#888"}.get(
                cr.get("alignment","neutral").lower(), "#888")
            cr_rows += (
                f'<tr style="border-top:0.5px solid #E8E6DF">'
                f'<td style="padding:5px 8px;font-weight:500">{cr.get("symbol","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px">{cr.get("fom_view","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px">{cr.get("action","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px;color:#5F5E5A">{cr.get("comment","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px">{cr.get("your_signal","")}</td>'
                f'<td style="padding:5px 8px;font-size:12px;color:{align_color};font-weight:500">'
                f'{cr.get("alignment","")}</td></tr>'
            )
        cr_table = f"""
        <div style="margin-top:10px">
          <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:4px">Your Portfolio — Cross-Reference</div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#F7F5EE">
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Symbol</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">FOM View</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Action</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Comment</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Your Signal</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Alignment</th>
            </tr></thead>
            <tbody>{cr_rows}</tbody>
          </table>
        </div>""" if cr_rows else "<p style='color:#888;font-size:13px'>No portfolio symbols mentioned.</p>"

        week_outlook = yt_analysis.get("week_outlook","")

        yt_sections_html += f"""
        <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
          <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7;display:flex;align-items:center;gap:12px">
            <span style="font-size:18px">📺</span>
            <div>
              <span style="font-weight:500;font-size:14px">{yt_analysis.get('channel','')}</span>
              <span style="color:#5F5E5A;font-size:12px;margin-left:8px">{yt_analysis.get("published","")}</span>
              <span style="color:{bias_color};font-weight:500;font-size:12px;margin-left:8px">
                {yt_analysis.get("overall_bias","").title()} bias
              </span>
            </div>
            <a href="{yt_analysis.get("url","")}" target="_blank"
               style="margin-left:auto;font-size:12px;color:#4ca3ff;text-decoration:none">Watch ↗</a>
          </div>
          <div style="padding:12px 14px">
            <p style="margin:0 0 10px;font-size:13px;font-style:italic;color:#2C2C2A;line-height:1.6">
              {yt_analysis.get("summary","")}
            </p>
            {f'<p style="margin:0 0 10px;font-size:13px;color:#1D9E75"><strong>Week outlook:</strong> {week_outlook}</p>' if week_outlook else ""}
            {pt_table}
            {cr_table}
          </div>
        </div>"""

    # ── Transcript appendix ────────────────────────────────────────────────
    transcripts_html = ""
    for yt in (yt_analyses or []):
        transcript = yt.get("transcript", "")
        if not transcript:
            continue
        words  = transcript.split()
        chunks = [" ".join(words[i:i+300]) for i in range(0, len(words), 300)]
        chunk_html = "".join(
            f'<p style="margin:0 0 12px;line-height:1.7">{c}</p>'
            for c in chunks
        )
        transcripts_html += f"""
        <div style="margin-bottom:24px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
          <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7;
                      display:flex;align-items:center;gap:8px">
            <span style="font-size:16px">📄</span>
            <span style="font-weight:500;font-size:14px">Full Transcript — {yt.get('channel','')}</span>
            <em style="font-weight:400;font-size:13px;color:#5F5E5A">"{yt.get('title','')}"</em>
            <span style="margin-left:auto;font-size:11px;color:#888">{len(words):,} words</span>
          </div>
          <div style="padding:16px;font-size:12px;color:#2C2C2A;
                      font-family:Georgia,serif;line-height:1.8;
                      max-height:500px;overflow-y:auto;background:#FAFAF8">
            {chunk_html}
          </div>
        </div>"""

    # ── Expected move section ──────────────────────────────────────────────
    em_section_html = format_em_html(em_data or {})

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:860px;margin:0 auto;padding:20px;color:#2C2C2A;background:#fff;font-size:14px}}</style>
</head><body>
<div style="border-bottom:2px solid #1D9E75;padding-bottom:12px;margin-bottom:20px">
  <h1 style="margin:0;font-size:20px;font-weight:500">Morning Intelligence Report</h1>
  <p style="margin:4px 0 0;font-size:13px;color:#5F5E5A">{today_str} 7:30 AM ET &nbsp;|&nbsp; Portfolio: ${total_value:,.0f}</p>
  <p style="margin:4px 0 0;font-size:12px;color:#888">Reading material — trade confirmations arrive at 9:50 AM in the Opening Report</p>
</div>
{futures_html}
{overview_html}
{em_section_html}
{narrative_html}
{yt_sections_html}
{earn_html}
{watchlist_section}
{gaps_section}
{news_section}
{transcripts_html}
<div style="border-top:0.5px solid #D3D1C7;padding-top:12px;margin-top:8px;font-size:11px;color:#888">
  Pre-market data is low-volume and may not reflect open prices.
  Wait for 9:50 AM opening report before executing.
</div>
</body></html>"""


def build_morning_text(
    today_str: str,
    market_overview: dict,
    active_signals: list,
    gap_alerts: list,
    news_data: dict,
    earnings_alerts: list,
    morning_narrative: str,
    total_value: float,
    yt_analyses: list = None,
    em_data: dict = None,
    futures_snap: list = None,
    top_headline: dict = None,
) -> str:
    spy_gap = market_overview.get("spy", {}).get("gap_pct", 0)
    qqq_gap = market_overview.get("qqq", {}).get("gap_pct", 0)
    vix     = market_overview.get("vix", {}).get("price", "N/A")
    vix_lvl = market_overview.get("vix", {}).get("level", "normal")

    lines = [
        "═" * 62,
        f"  MORNING INTELLIGENCE — {today_str} 7:30 AM ET",
        f"  Portfolio: ${total_value:,.0f}",
        "═" * 62,
        "",
    ]

    # ── Futures snapshot ───────────────────────────────────────────────────
    if futures_snap:
        lines += ["MARKET SNAPSHOT:", format_futures_text(futures_snap), ""]
    if top_headline and top_headline.get("title"):
        lines += [f"  📰 {top_headline['title']}", ""]

    lines += [
        f"  SPY {spy_gap:+.2f}%  |  QQQ {qqq_gap:+.2f}%  |  VIX {vix} ({vix_lvl})",
        "",
        f"  {morning_narrative}",
        "",
    ]

    # ── Expected move ──────────────────────────────────────────────────────
    em_text = format_em_text(em_data or {})
    if em_text:
        lines += [em_text, ""]

    # ── YouTube sections ───────────────────────────────────────────────────
    for yt_analysis in (yt_analyses or []):
        if not yt_analysis.get("error"):
            bias = yt_analysis.get("overall_bias","?")
            lines += [
                f"{'─'*62}",
                f"  📺 {yt_analysis.get('channel','')} — {yt_analysis.get('title','')}",
                f"  Bias: {bias.upper()}  |  {yt_analysis.get('published','')}",
                f"  {yt_analysis.get('summary','')}",
            ]
            if yt_analysis.get("week_outlook"):
                lines.append(f"  Week outlook: {yt_analysis['week_outlook']}")
            lines.append("")

    # ── Earnings alerts ────────────────────────────────────────────────────
    if earnings_alerts:
        lines += [f"{'─'*62}", "⚠️  EARNINGS ALERTS:"]
        for e in earnings_alerts:
            lines.append(f"  {e['symbol']:6} — {e['note']}  [{e['account']}]")
        lines.append("")

    # ── Watchlist ──────────────────────────────────────────────────────────
    if active_signals:
        lines += [f"{'─'*62}", "TODAY'S WATCHLIST:"]
        for s in sorted(active_signals, key=lambda x: -x["conviction"]):
            pm = s.get("gap_pct", 0)
            lines.append(
                f"  {s['symbol']:6} {s['eod_signal']:12} cv={s['conviction']:3d} "
                f"pre-mkt:{pm:+.1f}%  [{s['account']}]"
            )
        lines.append("")

    # ── Gap alerts ─────────────────────────────────────────────────────────
    if gap_alerts:
        lines += [f"{'─'*62}", "PRE-MARKET MOVES:"]
        for g in sorted(gap_alerts, key=lambda x: -abs(x["gap_pct"])):
            lines.append(
                f"  {g['symbol']:6} {g['gap_pct']:+.2f}%  "
                f"{gap_significance(g['gap_pct'],g.get('asset_class','etf')):12}  "
                f"EOD:{g.get('eod_signal','HOLD'):12}  [{g['account']}]"
            )
        lines.append("")

    # ── News sentiment ─────────────────────────────────────────────────────
    news_items = [(sym, nd) for sym, nd in news_data.items() if nd.get("headlines")]
    if news_items:
        lines += [f"{'─'*62}", "NEWS SENTIMENT:"]
        for sym, nd in sorted(news_items, key=lambda x: -abs(x[1].get("sentiment",0))):
            emoji = sentiment_emoji(nd.get("sentiment", 0))
            lines.append(
                f"  {sym:6} {emoji} {nd.get('sentiment_label','neutral'):14}  "
                f"{nd.get('sentiment_summary','')[:50]}"
            )
        lines.append("")

    lines += [
        "─" * 62,
        "  Pre-market data is low-volume — wait for 9:50 AM opening report.",
        "═" * 62,
    ]
    return "\n".join(lines)


def run():
    logger.info("=== Morning Intelligence Engine — 7:30 AM ===")

    # Auto-apply latest Fidelity CSV if found and newer than portfolio.json
    auto_update_portfolio()

    portfolio   = load_portfolio()
    tradeable   = get_tradeable_accounts(portfolio)
    signal_log  = load_signal_log()
    today_str   = datetime.today().strftime("%Y-%m-%d")
    total_value = sum(
        v.get("account_value", 0)
        for v in portfolio.get("accounts", {}).values()
    )
    ollama_ok = check_ollama_available()

    # Collect all symbols across tradeable accounts
    from run_eod import load_symbols_for_account
    all_symbols    = set()
    symbol_account = {}
    symbol_class   = {}
    for acct_name, acct_cfg in tradeable.items():
        syms = load_symbols_for_account(acct_name)
        if not syms:
            syms = list(acct_cfg.get("positions", {}).keys())
        for sym in syms:
            all_symbols.add(sym)
            if sym not in symbol_account:
                symbol_account[sym] = acct_name
                symbol_class[sym]   = acct_cfg.get("asset_class", "etf")
        for sym in acct_cfg.get("positions", {}).keys():
            all_symbols.add(sym)
            if sym not in symbol_account:
                symbol_account[sym] = acct_name
                symbol_class[sym]   = acct_cfg.get("asset_class", "etf")

    logger.info(f"Portfolio: ${total_value:,.0f} | {len(all_symbols)} symbols")

    # ── Futures + headline ─────────────────────────────────────────────────
    logger.info("Fetching futures + top headline...")
    futures_snap = get_futures_snapshot()
    top_headline = get_top_headline()
    fetched = sum(1 for f in futures_snap if f.get("price") is not None)
    logger.info(f"Futures: {fetched}/8 fetched | headline: {'yes' if top_headline.get('title') else 'no'}")

    # ── Pre-market quotes ──────────────────────────────────────────────────
    logger.info("Fetching pre-market quotes...")
    premarket = get_premarket_batch(list(all_symbols))
    market_ov = get_market_overview()
    logger.info(
        f"SPY {market_ov['spy']['gap_pct']:+.2f}% | "
        f"QQQ {market_ov['qqq']['gap_pct']:+.2f}% | "
        f"VIX {market_ov['vix']['price']}"
    )

    # ── Expected move ──────────────────────────────────────────────────────
    logger.info("Fetching expected move...")
    em_data = get_market_expected_moves()

    # ── YouTube ────────────────────────────────────────────────────────────
    logger.info("Fetching YouTube analyses...")
    yt_analyses = []
    try:
        yt_analyses = fetch_all_channels(list(symbol_account.keys()), signal_log)
    except Exception as e:
        logger.warning(f"YouTube fetch failed: {e}")

    # ── News sentiment ─────────────────────────────────────────────────────
    # Fetch news for: active signals + all held positions (capped at 12 total)
    # Do NOT gate on signal_log — on first run it's empty and we'd get no news.
    logger.info("Fetching news sentiment...")
    active_signal_syms = {
        k.split(":")[1]
        for k, v in signal_log.items()
        if v.get("signal") in ("BUY","SELL","STRONG_BUY","STRONG_SELL")
        and not v.get("blocked_by")
    }
    held_syms = {
        sym
        for acct_cfg in tradeable.values()
        for sym in acct_cfg.get("positions", {}).keys()
    }
    # Priority: active signals first, then held positions, cap at 12
    news_priority = list(active_signal_syms) + [s for s in held_syms if s not in active_signal_syms]
    news_syms = news_priority[:12]
    news_data = fetch_news_batch(news_syms) if news_syms else {}
    logger.info(f"News fetched for {len(news_data)} symbols ({len(active_signal_syms)} active signals + {len(held_syms)} held)")

    # ── Gap alerts ─────────────────────────────────────────────────────────
    gap_alerts = []
    for sym, q in premarket.items():
        gap_pct = q.get("gap_pct", 0)
        if abs(gap_pct) < 0.3:
            continue
        acct_name   = symbol_account.get(sym, "")
        asset_class = symbol_class.get(sym, "etf")
        eod_sig     = signal_log.get(f"{acct_name}:{sym}", {}).get("signal", "HOLD")
        gap_alerts.append({
            "symbol":     sym,
            "gap_pct":    gap_pct,
            "gap_label":  q.get("gap_label", "flat"),
            "account":    acct_name,
            "asset_class":asset_class,
            "eod_signal": eod_sig,
        })
    gap_alerts.sort(key=lambda x: -abs(x["gap_pct"]))
    logger.info(f"Gap alerts: {len(gap_alerts)} symbols with gap >= 0.3%")

    # ── Active signals ─────────────────────────────────────────────────────
    # Show BUY/SELL signals first; if signal_log is empty (first run),
    # fall back to showing all held positions so watchlist is never blank.
    active_signals = []
    for acct_name, acct_cfg in tradeable.items():
        for sym in acct_cfg.get("positions", {}).keys():
            key = f"{acct_name}:{sym}"
            sig = signal_log.get(key, {})
            action = sig.get("signal", "HOLD")
            # Include actionable signals OR all positions if signal_log empty
            if action in ("BUY","SELL","STRONG_BUY","STRONG_SELL") or not signal_log:
                if not sig.get("blocked_by"):
                    pm_q = premarket.get(sym, {})
                    active_signals.append({
                        "symbol":        sym,
                        "eod_signal":    action,
                        "conviction":    sig.get("conviction", 50),
                        "account":       acct_name,
                        "gap_pct":       pm_q.get("gap_pct", 0),
                        "suggested_usd": sig.get("suggested_usd", 0),
                    })
    logger.info(f"Active signals: {len(active_signals)} ({'from signal_log' if signal_log else 'first-run fallback — run run_eod.py tonight'})") 

    # ── Earnings alerts ────────────────────────────────────────────────────
    earnings_alerts = []
    for sym in all_symbols:
        if is_near_earnings(sym):
            earnings_alerts.append({
                "symbol":  sym,
                "account": symbol_account.get(sym, ""),
                "note":    "Earnings within 48h — BUY signal blocked",
            })

    # ── Morning narrative ──────────────────────────────────────────────────
    morning_narrative = _ollama_morning_narrative(
        ollama_ok, market_ov, active_signals, gap_alerts,
        [
            {
                "symbol":  sym,
                "emoji":   sentiment_emoji(nd.get("sentiment",0)),
                "label":   nd.get("sentiment_label","neutral"),
                "summary": nd.get("sentiment_summary",""),
            }
            for sym, nd in news_data.items()
        ]
    )

    # ── Build + deliver ────────────────────────────────────────────────────
    html_report = build_morning_html(
        today_str, market_ov, active_signals, gap_alerts,
        news_data, earnings_alerts, morning_narrative, total_value,
        yt_analyses=yt_analyses, em_data=em_data,
        futures_snap=futures_snap, top_headline=top_headline,
    )
    text_report = build_morning_text(
        today_str, market_ov, active_signals, gap_alerts,
        news_data, earnings_alerts, morning_narrative, total_value,
        yt_analyses=yt_analyses, em_data=em_data,
        futures_snap=futures_snap, top_headline=top_headline,
    )

    active_count = len(active_signals)
    subject = (
        f"SWING SIGNAL: Morning Intel {today_str} — "
        f"SPY {market_ov['spy']['gap_pct']:+.1f}% | "
        f"{active_count} signal{'s' if active_count!=1 else ''} to watch"
    )

    logger.info(f"\n{text_report}")
    deliver_report(subject, html_report, text_report)
    logger.info(f"Morning report done. Log: {_log_file}")


if __name__ == "__main__":
    run()