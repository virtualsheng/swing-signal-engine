"""
run_morning.py — Morning Intelligence Report (7:30 AM ET)
──────────────────────────────────────────────────────────
Overnight context before you execute anything. Reads last night's
EOD signals and enriches them with:
  - Pre-market price moves for all holdings
  - Gap analysis (which positions gapped overnight)
  - News sentiment per symbol (Yahoo Finance + Ollama)
  - Market overview: SPY/QQQ futures direction, VIX level
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
from notifications.notifier  import deliver_report

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

    # Market overview bar
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

    # AI narrative
    narrative_html = f"""
    <div style="margin-bottom:20px;padding:14px 16px;border-left:3px solid #1D9E75;background:#FAFAF8;border-radius:0 6px 6px 0">
      <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px">Morning Briefing</div>
      <p style="margin:0;font-size:13px;line-height:1.7;color:#2C2C2A">{morning_narrative}</p>
    </div>"""

    # Gap alerts
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

    # Active signals watchlist
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
            watch_note = f"Watch for confirmation above OR high at open"
        elif sig in ("SELL","STRONG_SELL"):
            watch_note = f"Watch for continuation lower — consider reducing"
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

    # News sentiment
    news_rows = ""
    for sym, nd in sorted(news_data.items(), key=lambda x: -abs(x[1].get("sentiment",0))):
        if not nd.get("headlines"):
            continue
        emoji  = sentiment_emoji(nd.get("sentiment", 0))
        label  = nd.get("sentiment_label", "neutral")
        summary = nd.get("sentiment_summary", "")[:80]
        catalyst = nd.get("catalyst", "none")
        cat_html = f'<span style="color:#BA7517;font-size:11px"> [{catalyst}]</span>' if catalyst and catalyst != "none" else ""
        recent = nd.get("headlines", [{}])[0].get("title", "")[:70]
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

    # Earnings alerts
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
        <div style="margin-bottom:20px;border:1px solid #F7C1C1;border-radius:8px;overflow:hidden;background:#FFF5F5">
          <div style="background:#FCEBEB;padding:10px 14px;border-bottom:1px solid #F7C1C1">
            <span style="font-weight:500;color:#A32D2D">⚠️ Earnings Alert — BUY signals blocked</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#FCEAEA">
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Account</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Note</th>
            </tr></thead>
            <tbody>{earn_rows}</tbody>
          </table>
        </div>"""

    # ── YouTube sections (one per channel) ───────────────────────────────────
    yt_sections_html = ""
    for yt_analysis in (yt_analyses or []):
      if True:
        bias_color = {"bullish":"#1D9E75","bearish":"#E24B4A",
                      "neutral":"#888780","mixed":"#BA7517","unknown":"#888780"}.get(
            yt_analysis.get("overall_bias","neutral"), "#888780")

        # Price targets table
        pts = yt_analysis.get("price_targets", [])
        pt_rows = ""
        for pt in pts:
            sym = pt.get("symbol","")
            tl  = pt.get("target_low");  th_v = pt.get("target_high")
            sl  = pt.get("support");     rs   = pt.get("resistance")
            er_l= pt.get("expected_range_low"); er_h = pt.get("expected_range_high")
            tf  = pt.get("timeframe","")
            notes = pt.get("notes","")[:60]
            range_str = (f"${er_l:.2f}–${er_h:.2f}" if er_l and er_h else
                         f"${tl:.2f}–${th_v:.2f}" if tl and th_v else "—")
            pt_rows += f"""
            <tr style="border-top:0.5px solid #E8E6DF">
              <td style="padding:7px 8px;font-weight:500">{sym}</td>
              <td style="padding:7px 8px;color:#1D9E75">{range_str}</td>
              <td style="padding:7px 8px;color:#E24B4A">{f"${sl:.2f}" if sl else "—"}</td>
              <td style="padding:7px 8px;color:#BA7517">{f"${rs:.2f}" if rs else "—"}</td>
              <td style="padding:7px 8px;font-size:11px;color:#5F5E5A">{tf}</td>
              <td style="padding:7px 8px;font-size:11px;color:#888">{notes}</td>
            </tr>"""

        pt_table = f"""
        <div style="margin-bottom:12px">
          <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px">Price Targets & Expected Ranges</div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#F7F5EE">
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Symbol</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Target/Range</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Support</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Resistance</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Timeframe</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:11px">Notes</th>
            </tr></thead>
            <tbody>{pt_rows}</tbody>
          </table>
        </div>""" if pt_rows else ""

        # Cross-reference with your signals
        cr = yt_analysis.get("cross_reference", [])
        portfolio_cr = [x for x in cr if x["alignment"] != "not_in_portfolio"]
        cr_rows = ""
        for x in portfolio_cr:
            align_color = {"aligned":"#1D9E75","conflict":"#E24B4A","neutral":"#888780"}.get(x["alignment"],"#888780")
            align_icon  = {"aligned":"✅","conflict":"⚠️","neutral":"—"}.get(x["alignment"],"—")
            sent_color  = {"bullish":"#1D9E75","bearish":"#E24B4A","cautious":"#BA7517","neutral":"#888780"}.get(x["yt_sentiment"],"#888780")
            eod_color   = {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75","SELL":"#E24B4A","STRONG_SELL":"#E24B4A"}.get(x.get("eod_signal",""),"#888780")
            action      = x.get("action_mentioned","")
            cr_rows += f"""
            <tr style="border-top:0.5px solid #E8E6DF">
              <td style="padding:7px 8px;font-weight:500">{x["symbol"]}</td>
              <td style="padding:7px 8px;color:{sent_color}">{x["yt_sentiment"].title()}</td>
              <td style="padding:7px 8px;font-size:11px;color:#888">{action}</td>
              <td style="padding:7px 8px;font-size:12px;color:#5F5E5A">{x["yt_comment"][:65]}</td>
              <td style="padding:7px 8px;color:{eod_color}">{x.get("eod_signal","—")}</td>
              <td style="padding:7px 8px;color:{align_color};font-weight:500">{align_icon} {x["alignment"].replace("_"," ").title()}</td>
            </tr>"""

        cr_table = f"""
        <div style="margin-bottom:12px">
          <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px">Your Portfolio — Cross-Reference</div>
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

    # ── Transcript appendix ───────────────────────────────────────────────────
    transcripts_html = ""
    for yt in (yt_analyses or []):
        transcript = yt.get("transcript", "")
        if not transcript:
            continue
        # Clean up transcript for display
        # Split into paragraphs every ~300 words for readability
        words = transcript.split()
        chunks = []
        for i in range(0, len(words), 300):
            chunks.append(" ".join(words[i:i+300]))
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

    # Expected move HTML section
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
  Pre-market data is low-volume and may not reflect open prices. Wait for 9:50 AM opening report before executing.
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
) -> str:
    spy_gap = market_overview.get("spy", {}).get("gap_pct", 0)
    qqq_gap = market_overview.get("qqq", {}).get("gap_pct", 0)
    vix     = market_overview.get("vix", {}).get("price", "N/A")
    vix_lvl = market_overview.get("vix", {}).get("level", "normal")

    lines = [
        "═" * 62,
        f"  MORNING INTELLIGENCE — {today_str} 7:30 AM ET",
        f"  Portfolio: ${total_value:,.0f}",
        "═" * 62, "",
        f"  SPY {spy_gap:+.2f}%  |  QQQ {qqq_gap:+.2f}%  |  VIX {vix} ({vix_lvl})",
        "",
        f"  {morning_narrative}",
        "",
    ]

    # YouTube sections
    for yt_analysis in (yt_analyses or []):
      if not yt_analysis.get("error"):
        bias = yt_analysis.get("overall_bias","?").upper()
        lines += [
            f"  📺 FIGURING OUT MONEY — {yt_analysis.get('published','')} | {bias} BIAS",
            "  Title: " + yt_analysis.get("title",""),
            f"  {yt_analysis.get('summary','')}",
        ]
        week = yt_analysis.get("week_outlook","")
        if week:
            lines.append(f"  Week: {week}")
        pts = yt_analysis.get("price_targets",[])
        if pts:
            lines.append("  Price targets / expected ranges:")
            for pt in pts:
                sym  = pt.get("symbol","")
                erl  = pt.get("expected_range_low") or pt.get("target_low")
                erh  = pt.get("expected_range_high") or pt.get("target_high")
                sup  = pt.get("support")
                res  = pt.get("resistance")
                tf   = pt.get("timeframe","")
                rng  = f"${erl:.2f}–${erh:.2f}" if erl and erh else "—"
                lines.append(
                    f"    {sym:6} range={rng:15} "
                    f"sup={f'${sup:.2f}' if sup else '—':8} "
                    f"res={f'${res:.2f}' if res else '—':8} {tf}"
                )
        cr = yt_analysis.get("cross_reference",[])
        conflicts = [x for x in cr if x["alignment"] == "conflict"]
        if conflicts:
            lines.append("  ⚠️  SIGNAL CONFLICTS vs your positions:")
            for x in conflicts:
                lines.append(
                    f"    {x['symbol']:6} FOM:{x['yt_sentiment']:8} "
                    f"Your signal:{x.get('eod_signal','—'):12} ← REVIEW"
                )
        lines.append(f"  Watch: {yt_analysis.get('url','')}")
        lines.append("")

    # Expected move section
    if em_data:
        lines.append(format_em_text(em_data))
        lines.append("")

    if earnings_alerts:
        lines.append("  ⚠️ EARNINGS ALERTS (BUY blocked):")
        for e in earnings_alerts:
            lines.append(f"    {e['symbol']:6} {e['note']}")
        lines.append("")

    if active_signals:
        lines.append("  TODAY'S WATCHLIST (from last night's EOD signals):")
        for s in sorted(active_signals, key=lambda x: -x["conviction"]):
            pm = s.get("gap_pct", 0)
            news = news_data.get(s["symbol"], {})
            sent_e = sentiment_emoji(news.get("sentiment", 0))
            lines.append(
                f"  {'🟢' if 'BUY' in s['eod_signal'] else '🔴'} "
                f"{s['symbol']:6} {s['eod_signal']:12} cv={s['conviction']:3d} "
                f"pm={pm:+.1f}%  news:{sent_e}  ${s.get('suggested_usd',0):,.0f}  [{s['account']}]"
            )
        lines.append("")

    if gap_alerts:
        lines.append("  PRE-MARKET MOVES:")
        for g in sorted(gap_alerts, key=lambda x: -abs(x["gap_pct"]))[:8]:
            lines.append(f"    {g['symbol']:6} {g['gap_pct']:+.1f}%  {g['gap_label']:12} [{g['account']}]")
        lines.append("")

    lines += [
        "─" * 62,
        "  Trade confirmations at 9:50 AM in the Opening Report.",
        "═" * 62,
    ]
    return "\n".join(lines)


def run():
    logger.info("=== Morning Intelligence Engine — 7:30 AM ===")

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
    # Load symbols from symbols.txt (full watchlist), fall back to portfolio positions
    from run_eod import load_symbols_for_account
    all_symbols    = set()
    symbol_account = {}  # symbol → account_name (first account wins for shared symbols)
    symbol_class   = {}  # symbol → asset_class
    for acct_name, acct_cfg in tradeable.items():
        syms = load_symbols_for_account(acct_name)
        if not syms:
            syms = list(acct_cfg.get("positions", {}).keys())
        for sym in syms:
            all_symbols.add(sym)
            if sym not in symbol_account:  # first account wins
                symbol_account[sym] = acct_name
                symbol_class[sym]   = acct_cfg.get("asset_class", "etf")
        # Also include portfolio positions (for position context)
        for sym in acct_cfg.get("positions", {}).keys():
            all_symbols.add(sym)
            if sym not in symbol_account:
                symbol_account[sym] = acct_name
                symbol_class[sym]   = acct_cfg.get("asset_class", "etf")

    logger.info(f"Portfolio: ${total_value:,.0f} | {len(all_symbols)} symbols")

    # Pre-market quotes
    logger.info("Fetching pre-market quotes...")
    premarket = get_premarket_batch(list(all_symbols))
    market_ov = get_market_overview()
    logger.info(f"SPY {market_ov['spy']['gap_pct']:+.2f}% | "
                f"QQQ {market_ov['qqq']['gap_pct']:+.2f}% | "
                f"VIX {market_ov['vix']['price']}")

    # News sentiment for active signals + biggest gaps
    logger.info("Fetching news sentiment...")
    active_signal_syms = {
        k.split(":")[1]
        for k, v in signal_log.items()
        if v.get("signal") in ("BUY","SELL","STRONG_BUY","STRONG_SELL")
        and not v.get("blocked_by")
    }
    gap_syms = {
        sym for sym, q in premarket.items()
        if abs(q.get("gap_pct", 0)) >= 1.0
    }
    # Cap news to active signal symbols only (max 8) — gap symbols excluded
    # to keep the morning run under 2 minutes. Keyword fallback runs instantly
    # without Ollama; Ollama grading fires only if available and under the cap.
    news_syms = list(active_signal_syms)[:8]
    logger.info(f"Fetching news for {len(news_syms)} active-signal symbols (capped at 8)...")
    news_data = fetch_news_batch(news_syms)   # always fetch — keyword fallback if Ollama down
    logger.info(f"News fetched for {len(news_data)} symbols")

    # ── Options implied expected move (SPY + QQQ) ────────────────────────────
    logger.info("Fetching options expected moves...")
    em_data = get_market_expected_moves()

    # ── YouTube: FiguringOutMoney latest video ────────────────────────────────
    logger.info("Checking YouTube channels for new videos...")
    yt_analyses = []
    if ollama_ok:
        try:
            yt_analyses = fetch_all_channels(
                portfolio_symbols=list(all_symbols),
                signal_log=load_signal_log(),
            )
            for yt in yt_analyses:
                if not yt.get("error"):
                    pts = yt.get("price_targets", [])
                    logger.info(f"  📺 {yt['channel']}: '{yt['title']}' "
                                f"— {yt.get('overall_bias','?')} bias "
                                f"| {len(pts)} price target(s)")
                else:
                    logger.info(f"  📺 {yt['channel']}: '{yt['title']}' — transcript unavailable")
            if not yt_analyses:
                logger.info("  No new videos from any channel")
        except Exception as e:
            logger.warning(f"YouTube fetch failed: {e}")
    else:
        logger.info("YouTube analysis skipped (Ollama unavailable)")

    # Build gap alerts list — only include genuine pre-market prices
    # is_available=True means we got an actual pre-market quote, not just closing price
    gap_alerts = []
    for sym, q in premarket.items():
        gap = q.get("gap_pct", 0)
        # Only show if we have a real pre-market price AND it moved meaningfully
        if abs(gap) >= 0.3 and q.get("is_available", False):
            eod_key = f"{symbol_account.get(sym,'')}:{sym}"
            eod_sig = signal_log.get(eod_key, {}).get("signal", "HOLD")
            gap_alerts.append({
                "symbol":     sym,
                "gap_pct":    gap,
                "gap_label":  q.get("gap_label", "flat"),
                "data_type":  q.get("data_type", "pre-market"),
                "account":    symbol_account.get(sym, ""),
                "asset_class": symbol_class.get(sym, "etf"),
                "eod_signal": eod_sig,
            })
    gap_alerts.sort(key=lambda x: -abs(x["gap_pct"]))

    # Build active signals list
    active_signals = []
    for acct_name, acct_cfg in tradeable.items():
        acct_val = acct_cfg.get("account_value", 0)
        for sym in acct_cfg.get("positions", {}).keys():
            key = f"{acct_name}:{sym}"
            sig = signal_log.get(key, {})
            if sig.get("signal") in ("BUY","SELL","STRONG_BUY","STRONG_SELL") \
               and not sig.get("blocked_by"):
                pm_q = premarket.get(sym, {})
                active_signals.append({
                    "symbol":       sym,
                    "eod_signal":   sig.get("signal","HOLD"),
                    "conviction":   sig.get("conviction", 50),
                    "account":      acct_name,
                    "gap_pct":      pm_q.get("gap_pct", 0),
                    "suggested_usd": sig.get("suggested_usd", 0),
                })

    # Earnings alerts
    earnings_alerts = []
    for sym in all_symbols:
        if is_near_earnings(sym):
            acct = symbol_account.get(sym, "")
            earnings_alerts.append({
                "symbol":  sym,
                "account": acct,
                "note":    "Earnings within 48h — BUY signal blocked",
            })

    # Morning narrative
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

    # Build + deliver
    html_report = build_morning_html(
        today_str, market_ov, active_signals, gap_alerts,
        news_data, earnings_alerts, morning_narrative, total_value,
        yt_analyses=yt_analyses, em_data=em_data,
    )
    text_report = build_morning_text(
        today_str, market_ov, active_signals, gap_alerts,
        news_data, earnings_alerts, morning_narrative, total_value,
        yt_analyses=yt_analyses, em_data=em_data,
    )

    active_count = len(active_signals)
    subject = (f"SWING SIGNAL: Morning Intel {today_str} — "
               f"SPY {market_ov['spy']['gap_pct']:+.1f}% | "
               f"{active_count} signal{'s' if active_count!=1 else ''} to watch")

    logger.info(f"\n{text_report}")
    deliver_report(subject, html_report, text_report)
    logger.info(f"Morning report done. Log: {_log_file}")


if __name__ == "__main__":
    run()