"""
report_builder.py — Full HTML + text report
─────────────────────────────────────────────
Sections:
  0. Market close snapshot  — CNBC-style closing price bar (EOD only)
  1. Portfolio dashboard    — today's P&L per account, allocation, concentration
  2. Market narrative       — AI-generated 4–6 sentence market summary
  3. Per-account signals    — action required / watching / blocked
  4. Full technical scorecards — every symbol gets a complete breakdown
  5. 401(k) monitor         — no signals, just current values
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SIGNAL_EMOJI = {
    "BUY": "🟢", "STRONG_BUY": "🟢🟢",
    "SELL": "🔴", "STRONG_SELL": "🔴🔴",
    "HOLD": "🟡",
}

BAR_CHAR   = "█"
EMPTY_CHAR = "░"
BAR_WIDTH  = 10


def _conviction_bar(c: int) -> str:
    f = int(c / 100 * BAR_WIDTH)
    return BAR_CHAR * f + EMPTY_CHAR * (BAR_WIDTH - f)


# ── Portfolio summary builder ─────────────────────────────────────────────────

def build_portfolio_summary(signals_by_account: dict, portfolio: dict) -> dict:
    """
    Compute today's estimated P&L per account from 1-day price changes.
    Returns portfolio_summary dict for market narrative + dashboard.
    """
    accounts_out = []
    total_value  = 0.0
    total_pnl    = 0.0

    for acct_name, acct_config in portfolio.get("accounts", {}).items():
        acct_val  = acct_config.get("account_value", 0)
        total_value += acct_val
        positions = acct_config.get("positions", {})

        pnl_today = 0.0
        for sym, pos in positions.items():
            shares = pos.get("shares", 0)
            price  = pos.get("current_price", 0)
            acct_signals = signals_by_account.get(acct_name, [])
            sig = next((s for s in acct_signals if s["symbol"] == sym), None)
            if sig:
                chg_1d     = sig.get("scorecard", {}).get("chg_1d", 0) if "scorecard" in sig else sig.get("chg_1d", 0)
                prev_price = price / (1 + chg_1d / 100) if chg_1d != -100 else price
                pnl_today += (price - prev_price) * shares

        pnl_pct = pnl_today / acct_val * 100 if acct_val > 0 else 0
        total_pnl += pnl_today
        accounts_out.append({
            "name":      acct_name,
            "value":     acct_val,
            "pnl_today": pnl_today,
            "pnl_pct":   pnl_pct,
        })

    return {
        "total_value":     total_value,
        "total_pnl_today": total_pnl,
        "total_pnl_pct":   total_pnl / total_value * 100 if total_value > 0 else 0,
        "accounts":        accounts_out,
    }


def get_top_movers(signals_by_account: dict, n: int = 5) -> list:
    movers = []
    for acct_name, signals in signals_by_account.items():
        for s in signals:
            sc    = s.get("scorecard", {})
            chg1d = sc.get("chg_1d", s.get("chg_1d", 0))
            movers.append({
                "symbol":     s["symbol"],
                "chg_1d":     chg1d,
                "signal":     s["signal"],
                "conviction": s.get("conviction", 50),
                "account":    acct_name,
            })
    movers.sort(key=lambda x: abs(x["chg_1d"]), reverse=True)
    return movers[:n]


# ── HTML report ───────────────────────────────────────────────────────────────

def build_html_report(
    signals_by_account: dict,
    monitor_data: dict,
    regime: dict,
    report_type: str,
    total_value: float,
    market_narrative: str = "",
    portfolio_summary: dict = None,
    portfolio: dict = None,
    futures_snap: list = None,
    top_headline: dict = None,
) -> str:
    portfolio = portfolio or {}
    now   = datetime.now().strftime("%B %d, %Y %H:%M ET")
    label = "Pre-Market" if report_type == "PREMARKET" else "End of Day"

    regime_color = {
        "trending_up":   "#1D9E75",
        "trending_down": "#E24B4A",
        "ranging":       "#BA7517",
        "volatile":      "#7F77DD",
    }.get(regime.get("regime", ""), "#888780")

    def sig_color(s):
        return {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75",
                "SELL":"#E24B4A","STRONG_SELL":"#E24B4A","HOLD":"#BA7517"}.get(s,"#888780")

    def chg_color(v):
        return "#1D9E75" if v >= 0 else "#E24B4A"

    def bar_html(c):
        f = int(c / 100 * 10)
        return (f'<span style="color:#1D9E75;font-family:monospace;letter-spacing:-1px">{"█"*f}</span>'
                f'<span style="color:#D3D1C7;font-family:monospace;letter-spacing:-1px">{"░"*(10-f)}</span>'
                f'&nbsp;<span style="color:#888;font-size:11px">{c}</span>')

    def chk(ok):
        return '<span style="color:#1D9E75">✓</span>' if ok else '<span style="color:#E24B4A">✗</span>'

    # ── 0. CNBC-style market snapshot bar ─────────────────────────────────────
    market_bar_html = ""
    if futures_snap:
        try:
            from signals.market_futures import format_futures_html
            market_bar_html = format_futures_html(futures_snap, top_headline or {})
        except ImportError:
            pass

    # ── 1. Portfolio dashboard ─────────────────────────────────────────────────
    dashboard_html = ""
    if portfolio_summary:
        acct_cards = ""
        for a in portfolio_summary.get("accounts", []):
            pnl_c = chg_color(a["pnl_today"])
            acct_cards += f"""
            <div style="background:#F7F5EE;border-radius:6px;padding:10px 14px;min-width:160px">
              <div style="font-size:12px;color:#5F5E5A;margin-bottom:2px">{a['name']}</div>
              <div style="font-size:16px;font-weight:500">${a['value']:,.0f}</div>
              <div style="font-size:12px;color:{pnl_c};margin-top:2px">{a['pnl_today']:+,.0f} ({a['pnl_pct']:+.2f}%)</div>
            </div>"""
        total_pnl = portfolio_summary.get("total_pnl_today", 0)
        total_pct = portfolio_summary.get("total_pnl_pct", 0)
        tp_color  = chg_color(total_pnl)
        dashboard_html = f"""
        <div style="margin-bottom:20px;padding:14px;background:#F1EFE8;border-radius:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div>
              <span style="font-size:15px;font-weight:500">Portfolio Dashboard</span>
              <span style="font-size:12px;color:#5F5E5A;margin-left:10px">Total: ${total_value:,.0f}</span>
            </div>
            <div style="text-align:right">
              <span style="font-size:15px;font-weight:500;color:{tp_color}">{total_pnl:+,.0f}</span>
              <span style="font-size:12px;color:{tp_color};margin-left:4px">({total_pct:+.2f}% today)</span>
            </div>
          </div>
          <div style="display:flex;gap:10px;flex-wrap:wrap">{acct_cards}</div>
        </div>"""

    # ── 2. Market narrative ────────────────────────────────────────────────────
    narrative_html = ""
    if market_narrative:
        narrative_html = f"""
        <div style="margin-bottom:20px;padding:14px 16px;border-left:3px solid {regime_color};background:#FAFAF8;border-radius:0 6px 6px 0">
          <div style="font-size:12px;font-weight:500;color:#5F5E5A;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px">Market Narrative — {label}</div>
          <p style="margin:0;font-size:13px;line-height:1.7;color:#2C2C2A">{market_narrative}</p>
        </div>"""

    # ── 3. Per-account signal sections ────────────────────────────────────────
    account_sections = ""
    for account_name, signals in signals_by_account.items():
        acct_val   = (signals[0]["account_value"] if signals
                      else portfolio.get("accounts", {}).get(account_name, {}).get("account_value", 0))
        actionable = [s for s in signals if s["signal"] in ("BUY","SELL","STRONG_BUY","STRONG_SELL") and not s.get("blocked_by")]
        watching   = [s for s in signals if s["signal"] == "HOLD" and s["conviction"] >= 55 and not s.get("blocked_by")]
        blocked_s  = [s for s in signals if s.get("blocked_by")]

        def signal_rows(items, show_size=True):
            rows = ""
            for s in sorted(items, key=lambda x: -x["conviction"]):
                sig    = s.get("ai_action", s["signal"])
                color  = sig_color(sig)
                sc     = s.get("scorecard", {})
                chg1d  = sc.get("chg_1d", s.get("chg_1d", 0))
                chg_c  = chg_color(chg1d)

                held_html = ""
                if s.get("held") and s.get("avg_cost"):
                    u  = s.get("unrealized_pct", 0)
                    uc = chg_color(u)
                    held_html = f'<br><span style="font-size:11px;color:{uc}">held {s.get("shares",0):.0f}sh @ ${s["avg_cost"]:.2f} ({u:+.1f}%)</span>'

                size_html = ""
                if show_size and s["signal"] in ("BUY","STRONG_BUY"):
                    sz  = s.get("suggested_usd", 0)
                    sp  = s.get("suggested_pct", 0)
                    pos_val  = s.get("shares", 0) * s.get("price", 0)
                    cur_pct  = pos_val / acct_val * 100 if acct_val > 0 else 0
                    conc_warn = ""
                    if cur_pct > 12:
                        conc_warn = f'<br><span style="color:#E24B4A;font-size:11px">⚠️ already {cur_pct:.0f}% of account</span>'
                    size_html = f'<span style="color:#1D9E75;font-weight:500">${sz:,.0f}</span><span style="font-size:11px;color:#888"> ({sp:.1f}%)</span>{conc_warn}'

                blocked_html = f'<span style="color:#E24B4A;font-size:12px">⛔ {s["blocked_by"]}</span>' if s.get("blocked_by") else ""
                narrative    = s.get("narrative", "")

                rows += f"""
                <tr style="border-top:0.5px solid #E8E6DF">
                  <td style="padding:8px 8px;font-weight:500;white-space:nowrap">{s['symbol']}{held_html}</td>
                  <td style="padding:8px">{bar_html(s['conviction'])}</td>
                  <td style="padding:8px;color:{color};font-weight:500;white-space:nowrap">{sig}</td>
                  <td style="padding:8px;text-align:right;white-space:nowrap">
                    ${s['price']:.2f}<br>
                    <span style="font-size:11px;color:{chg_c}">{chg1d:+.2f}% 1d</span>
                  </td>
                  <td style="padding:8px;text-align:right">{size_html or blocked_html or "—"}</td>
                  <td style="padding:8px;font-size:12px;color:#5F5E5A">{s.get('reason','')[:55]}</td>
                </tr>
                {"<tr><td colspan='6' style='padding:2px 8px 10px;font-size:12px;color:#5F5E5A;font-style:italic'>" + narrative + "</td></tr>" if narrative else ""}"""
            return rows

        th  = 'style="padding:6px 8px;text-align:left;font-weight:400;color:#5F5E5A;font-size:12px;background:#F7F5EE"'
        thr = 'style="padding:6px 8px;text-align:right;font-weight:400;color:#5F5E5A;font-size:12px;background:#F7F5EE"'
        thead = f'<tr><th {th}>Symbol</th><th {th}>Conviction</th><th {th}>Signal</th><th {thr}>Price / 1d</th><th {thr}>Size</th><th {th}>Reason</th></tr>'

        if actionable:
            action_block = f"""
            <div style="margin-bottom:14px">
              <div style="font-size:13px;font-weight:500;margin-bottom:6px">⚡ Action Required</div>
              <table style="width:100%;border-collapse:collapse;font-size:13px">
                <thead>{thead}</thead><tbody>{signal_rows(actionable)}</tbody>
              </table>
            </div>"""
        else:
            action_block = '<div style="font-size:13px;color:#888;margin-bottom:12px">No actionable signals today.</div>'

        watch_block = f"""
        <div style="margin-bottom:14px">
          <div style="font-size:13px;font-weight:500;margin-bottom:6px">👁 Watching</div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead><tbody>{signal_rows(watching, show_size=False)}</tbody>
          </table>
        </div>""" if watching else ""

        blocked_block = f"""
        <div style="margin-bottom:14px">
          <div style="font-size:13px;font-weight:500;margin-bottom:6px">⛔ Blocked</div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>{thead}</thead><tbody>{signal_rows(blocked_s, show_size=False)}</tbody>
          </table>
        </div>""" if blocked_s else ""

        account_sections += f"""
        <div style="margin-bottom:24px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
          <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7;display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:500;font-size:15px">{account_name}</span>
            <span style="color:#5F5E5A;font-size:13px">${acct_val:,.0f} &nbsp;|&nbsp; {len(signals)} symbols</span>
          </div>
          <div style="padding:12px 14px">
            {action_block}{watch_block}{blocked_block}
          </div>
        </div>"""

    # ── 4. Full technical scorecard section ────────────────────────────────────
    scorecard_sections = ""
    for account_name, signals in signals_by_account.items():
        if not signals:
            continue
        acct_val = (signals[0]["account_value"] if signals
                    else portfolio.get("accounts", {}).get(account_name, {}).get("account_value", 0))
        rows = ""
        for s in sorted(signals, key=lambda x: (-x["conviction"], x["symbol"])):
            sc    = s.get("scorecard", {})
            sig   = s.get("ai_action", s["signal"])
            color = {"BUY":"#1D9E75","STRONG_BUY":"#1D9E75",
                     "SELL":"#E24B4A","STRONG_SELL":"#E24B4A","HOLD":"#BA7517"}.get(sig,"#888780")

            held_row = ""
            if s.get("held") and s.get("avg_cost") and s.get("shares"):
                cost_basis = s["avg_cost"] * s["shares"]
                curr_val   = s["price"] * s["shares"]
                unreal_d   = curr_val - cost_basis
                unreal_p   = s.get("unrealized_pct", 0)
                uc = "#1D9E75" if unreal_d >= 0 else "#E24B4A"
                held_row = f"""
                <tr style="background:#FAFAF8">
                  <td colspan="2" style="padding:4px 8px;font-size:12px;color:#5F5E5A">
                    Position: {s['shares']:.0f} shares @ ${s['avg_cost']:.2f} avg cost
                    &nbsp;|&nbsp; Cost basis: ${cost_basis:,.0f}
                    &nbsp;|&nbsp; Current: ${curr_val:,.0f}
                    &nbsp;|&nbsp; <span style="color:{uc};font-weight:500">Unrealized: ${unreal_d:+,.0f} ({unreal_p:+.1f}%)</span>
                    &nbsp;|&nbsp; Weight: {curr_val/acct_val*100:.1f}% of account
                  </td>
                </tr>"""

            pos_val  = s.get("shares", 0) * s["price"]
            pos_pct  = pos_val / acct_val * 100 if acct_val > 0 else 0
            conc_html = ""
            if pos_pct > 15:
                conc_html = f'&nbsp;<span style="color:#E24B4A;font-size:11px">⚠️ HIGH CONCENTRATION {pos_pct:.0f}%</span>'
            elif pos_pct > 10:
                conc_html = f'&nbsp;<span style="color:#BA7517;font-size:11px">⚠️ concentrated {pos_pct:.0f}%</span>'

            chg1d  = sc.get("chg_1d",  s.get("chg_1d",  0))
            chg5d  = sc.get("chg_5d",  s.get("chg_5d",  0))
            chg20d = sc.get("chg_20d", 0)

            rows += f"""
            <tr style="border-top:1px solid #E8E6DF">
              <td style="padding:10px 8px;vertical-align:top;width:220px">
                <div style="font-weight:500;font-size:14px">{s['symbol']}{conc_html}</div>
                <div style="font-size:12px;color:#5F5E5A;margin-top:2px">${s['price']:.2f}</div>
                <div style="margin-top:4px">{bar_html(s['conviction'])}</div>
                <div style="margin-top:4px">
                  <span style="color:{color};font-weight:500;font-size:13px">{sig}</span>
                  <span style="font-size:11px;color:#888;margin-left:6px">AI {s.get('ai_confidence',0.5):.0%}</span>
                </div>
                <div style="font-size:11px;color:#5F5E5A;margin-top:4px">
                  <span style="color:{"#1D9E75" if chg1d>=0 else "#E24B4A"}">{chg1d:+.2f}% 1d</span>
                  &nbsp;
                  <span style="color:{"#1D9E75" if chg5d>=0 else "#E24B4A"}">{chg5d:+.2f}% 5d</span>
                  &nbsp;
                  <span style="color:{"#1D9E75" if chg20d>=0 else "#E24B4A"}">{chg20d:+.2f}% 20d</span>
                </div>
              </td>
              <td style="padding:10px 8px;vertical-align:top;font-size:12px">
                <table style="border-collapse:collapse;width:100%">
                  <tr>
                    <td style="padding:2px 12px 2px 0;color:#5F5E5A;white-space:nowrap">EMA 2/3/5</td>
                    <td style="padding:2px 0">{sc.get('ema_label','—')}</td>
                    <td style="padding:2px 0 2px 16px;color:#5F5E5A;white-space:nowrap">RSI(14)</td>
                    <td style="padding:2px 0">
                      <span style="color:{"#1D9E75" if sc.get('rsi',50)<40 else "#E24B4A" if sc.get('rsi',50)>65 else "#888"}">{sc.get('rsi',50):.1f}</span>
                      &nbsp;<span style="color:#888">{sc.get('rsi_label','')}</span>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:2px 12px 2px 0;color:#5F5E5A;white-space:nowrap">MACD</td>
                    <td style="padding:2px 0">{sc.get('macd_label','—')}</td>
                    <td style="padding:2px 0 2px 16px;color:#5F5E5A;white-space:nowrap">Hist</td>
                    <td style="padding:2px 0">{sc.get('macd_hist_label','—')}</td>
                  </tr>
                  <tr>
                    <td style="padding:2px 12px 2px 0;color:#5F5E5A;white-space:nowrap">SMA 50</td>
                    <td style="padding:2px 0">
                      {chk(sc.get('above_sma50'))}
                      <span style="color:#888">&nbsp;${sc.get('sma50',0):.2f}
                      ({(s['price']/sc['sma50']-1)*100:+.1f}%)</span>
                    </td>
                    <td style="padding:2px 0 2px 16px;color:#5F5E5A;white-space:nowrap">SMA 200</td>
                    <td style="padding:2px 0">
                      {chk(sc.get('above_sma200'))}
                      <span style="color:#888">&nbsp;${sc.get('sma200',0):.2f}
                      ({(s['price']/sc['sma200']-1)*100 if sc.get('sma200') else 0:+.1f}%)</span>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:2px 12px 2px 0;color:#5F5E5A;white-space:nowrap">Volume</td>
                    <td style="padding:2px 0">
                      <span style="color:{"#1D9E75" if sc.get('vol_ratio',1)>1.5 else "#888"}">{sc.get('vol_ratio',1):.2f}x</span>
                      &nbsp;<span style="color:#888">{sc.get('vol_label','avg')}</span>
                    </td>
                    <td style="padding:2px 0 2px 16px;color:#5F5E5A;white-space:nowrap">ATR</td>
                    <td style="padding:2px 0;color:#888">${sc.get('atr',0):.2f} ({sc.get('atr_pct',0):.1f}%)</td>
                  </tr>
                  <tr>
                    <td style="padding:2px 12px 2px 0;color:#5F5E5A;white-space:nowrap">52w range</td>
                    <td colspan="3" style="padding:2px 0">
                      <span style="color:#888">${sc.get('low_52w',0):.2f}</span>
                      &nbsp;
                      <span style="display:inline-block;width:80px;height:6px;background:#E8E6DF;border-radius:3px;vertical-align:middle;position:relative">
                        <span style="display:inline-block;position:absolute;left:{sc.get('range52_pct',50):.0f}%;top:-1px;width:8px;height:8px;border-radius:50%;background:#1D9E75;transform:translateX(-50%)"></span>
                      </span>
                      &nbsp;
                      <span style="color:#888">${sc.get('high_52w',0):.2f}</span>
                      &nbsp;
                      <span style="color:#5F5E5A;font-size:11px">({sc.get('range52_pct',50):.0f}% of range)</span>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:4px 12px 2px 0;color:#5F5E5A;white-space:nowrap">Score</td>
                    <td colspan="3" style="padding:4px 0;font-size:11px">
                      <span style="color:#1D9E75">Bull {sc.get('bull_score',0):.1f}</span>
                      &nbsp;vs&nbsp;
                      <span style="color:#E24B4A">Bear {sc.get('bear_score_val',0):.1f}</span>
                      &nbsp;=&nbsp;
                      <span style="font-weight:500;color:{"#1D9E75" if sc.get('net_score',0)>=0 else "#E24B4A"}">Net {sc.get('net_score',0):+.1f}</span>
                    </td>
                  </tr>
                </table>
                {f'<div style="margin-top:6px;font-size:11px;color:#5F5E5A;font-style:italic">{s.get("ai_reasoning","")}</div>' if s.get("ai_reasoning") else ""}
              </td>
            </tr>
            {held_row}"""

        scorecard_sections += f"""
        <div style="margin-bottom:24px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
          <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
            <span style="font-weight:500;font-size:15px">Technical Scorecard — {account_name}</span>
            <span style="color:#888;font-size:12px;margin-left:8px">All {len(signals)} symbols</span>
          </div>
          <table style="width:100%;border-collapse:collapse">
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    # ── 5. Monitor-only section ────────────────────────────────────────────────
    monitor_section = ""
    if monitor_data:
        mrows = ""
        for acct_name, data in monitor_data.items():
            for sym, pos in data["positions"].items():
                mrows += f"""
                <tr style="border-top:0.5px solid #E8E6DF">
                  <td style="padding:6px 8px;color:#888">{sym}</td>
                  <td style="padding:6px 8px;color:#888;font-size:12px">{pos.get('description',acct_name)[:40] if isinstance(pos,dict) else acct_name}</td>
                  <td style="padding:6px 8px;text-align:right;color:#888">${pos.get('current_value',0):,.0f}</td>
                  <td style="padding:6px 8px;text-align:right;color:#888">${pos.get('current_price',0):.2f}</td>
                  <td style="padding:6px 8px;text-align:right;color:#888">{pos.get('shares',0):.3f} sh</td>
                </tr>"""
        monitor_section = f"""
        <div style="margin-bottom:24px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
          <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
            <span style="font-weight:500;font-size:15px">Monitor Only — 401(k)</span>
            <span style="color:#888;font-size:12px;margin-left:8px">Mutual funds — no signals generated</span>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#F7F5EE">
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Fund</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px;text-align:right">Value</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px;text-align:right">NAV</th>
              <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px;text-align:right">Shares</th>
            </tr></thead>
            <tbody>{mrows}</tbody>
          </table>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:860px;margin:0 auto;padding:20px;color:#2C2C2A;background:#fff;font-size:14px}}
  @media(max-width:600px){{body{{padding:10px}}}}
</style>
</head><body>
<div style="border-bottom:2px solid #1D9E75;padding-bottom:12px;margin-bottom:20px">
  <h1 style="margin:0;font-size:20px;font-weight:500">Swing Signal Report — {label}</h1>
  <p style="margin:4px 0 0;font-size:13px;color:#5F5E5A">{now} &nbsp;|&nbsp; Total portfolio: ${total_value:,.0f}</p>
</div>
{market_bar_html}
<div style="background:#F1EFE8;border-left:3px solid {regime_color};padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:20px">
  <strong style="font-size:13px">Market Regime:</strong>
  <span style="color:{regime_color};font-weight:500"> {regime.get('regime','').replace('_',' ').title()}</span>
  <span style="color:#5F5E5A;font-size:12px"> — {regime.get('bias','').title()} — {regime.get('description','')}</span>
</div>
{dashboard_html}
{narrative_html}
<h2 style="font-size:16px;font-weight:500;margin:0 0 12px">Signals</h2>
{account_sections}
<h2 style="font-size:16px;font-weight:500;margin:24px 0 12px">Full Technical Scorecard</h2>
{scorecard_sections}
{monitor_section}
<div style="border-top:0.5px solid #D3D1C7;padding-top:12px;margin-top:8px;font-size:11px;color:#888">
  Signals are automated technical + AI analysis. Always verify before trading in Fidelity. Not financial advice.
</div>
</body></html>"""


# ── Plain text report ─────────────────────────────────────────────────────────

def build_text_report(
    signals_by_account: dict,
    monitor_data: dict,
    regime: dict,
    report_type: str,
    total_value: float,
    market_narrative: str = "",
    portfolio_summary: dict = None,
    portfolio: dict = None,
    futures_snap: list = None,
    top_headline: dict = None,
) -> str:
    portfolio = portfolio or {}
    now   = datetime.now().strftime("%b %d, %Y %H:%M ET")
    label = "PRE-MARKET" if report_type == "PREMARKET" else "EOD"

    lines = [
        "═" * 64,
        f"  SWING SIGNALS — {label} — {now}",
        f"  Total portfolio: ${total_value:,.0f}",
        "═" * 64,
        "",
    ]

    # ── Market closing snapshot ────────────────────────────────────────────────
    if futures_snap:
        try:
            from signals.market_futures import format_futures_text
            lines += ["MARKET CLOSE SNAPSHOT:", format_futures_text(futures_snap), ""]
        except ImportError:
            pass
    if top_headline and top_headline.get("title"):
        lines += [f"  📰 {top_headline['title']}", ""]

    # ── Portfolio dashboard ────────────────────────────────────────────────────
    if portfolio_summary:
        total_pnl = portfolio_summary.get("total_pnl_today", 0)
        total_pct = portfolio_summary.get("total_pnl_pct", 0)
        pnl_sign  = "+" if total_pnl >= 0 else ""
        lines.append(f"  TODAY'S P&L: {pnl_sign}${total_pnl:,.0f} ({pnl_sign}{total_pct:.2f}%)")
        for a in portfolio_summary.get("accounts", []):
            p = a["pnl_today"]; pp = a["pnl_pct"]
            lines.append(f"    {a['name']:<28} ${a['value']:>10,.0f}  {p:+,.0f} ({pp:+.2f}%)")
        lines.append("")

    # ── Regime ─────────────────────────────────────────────────────────────────
    regime_emoji = {"trending_up":"📈","trending_down":"📉",
                    "ranging":"↔️","volatile":"⚡"}.get(regime.get("regime",""),"❓")
    lines += [
        f"  MARKET REGIME: {regime_emoji} {regime.get('regime','').replace('_',' ').upper()}",
        f"  {regime.get('description','')}",
        "",
    ]

    # ── Market narrative ───────────────────────────────────────────────────────
    if market_narrative:
        lines += ["  MARKET COMMENTARY:", f"  {market_narrative}", ""]

    # ── Signals per account ────────────────────────────────────────────────────
    for account_name, signals in signals_by_account.items():
        acct_val = (signals[0]["account_value"] if signals
                    else portfolio.get("accounts", {}).get(account_name, {}).get("account_value", 0))
        lines += [
            "─" * 64,
            f"  {account_name}  ${acct_val:,.0f}",
            "─" * 64,
        ]

        actionable = [s for s in signals if s["signal"] in ("BUY","SELL","STRONG_BUY","STRONG_SELL") and not s.get("blocked_by")]
        watching   = [s for s in signals if s["signal"] == "HOLD" and s["conviction"] >= 55 and not s.get("blocked_by")]
        blocked_s  = [s for s in signals if s.get("blocked_by")]

        bar = lambda c: _conviction_bar(c)

        if actionable:
            lines.append("  ⚡ ACTION REQUIRED:")
            for s in sorted(actionable, key=lambda x: -x["conviction"]):
                sc    = s.get("scorecard", {})
                chg1d = sc.get("chg_1d", s.get("chg_1d", 0))
                emoji = SIGNAL_EMOJI.get(s["signal"], "")
                sz    = s.get("suggested_usd", 0)
                lines.append(
                    f"  {emoji} {s['symbol']:6} {bar(s['conviction'])} {s['conviction']:3d}  "
                    f"{s['signal']:12} ${s['price']:>8.2f} ({chg1d:+.2f}% 1d)  ${sz:,.0f}"
                )
                if s.get("narrative"):
                    lines.append(f"     {s['narrative']}")
            lines.append("")

        if watching:
            lines.append("  👁 WATCHING:")
            for s in sorted(watching, key=lambda x: -x["conviction"]):
                sc    = s.get("scorecard", {})
                chg1d = sc.get("chg_1d", s.get("chg_1d", 0))
                held  = f" [held {s.get('unrealized_pct',0):+.1f}%]" if s.get("held") else ""
                lines.append(
                    f"  🟡 {s['symbol']:6} {bar(s['conviction'])} {s['conviction']:3d}  "
                    f"HOLD         ${s['price']:>8.2f} ({chg1d:+.2f}% 1d){held}"
                )
            lines.append("")

        if blocked_s:
            lines.append("  ⛔ BLOCKED:")
            for s in blocked_s:
                lines.append(f"     {s['symbol']:6} {s['signal']:5} — {s['blocked_by']}")
            lines.append("")

        lines.append("  TECHNICAL SUMMARY:")
        for s in sorted(signals, key=lambda x: -x["conviction"]):
            sc    = s.get("scorecard", {})
            chg1d = sc.get("chg_1d", s.get("chg_1d", 0))
            lines.append(
                f"    {s['symbol']:6} {s['signal']:5} cv={s['conviction']:3d} "
                f"RSI={sc.get('rsi',50):5.1f} "
                f"{'▲SMA50' if sc.get('above_sma50') else '▼SMA50':7} "
                f"{'▲SMA200' if sc.get('above_sma200') else '▼SMA200':8} "
                f"MACD={'▲' if sc.get('macd_bull') else '▼'} "
                f"Vol={sc.get('vol_ratio',1):.1f}x "
                f"1d={chg1d:+.2f}%"
            )
        lines.append("")

    # ── Monitor ────────────────────────────────────────────────────────────────
    if monitor_data:
        lines += ["─" * 64, "  MONITOR ONLY — 401(k):", "─" * 64]
        for acct_name, data in monitor_data.items():
            for sym, pos in data["positions"].items():
                lines.append(f"    {sym:8} ${pos.get('current_value',0):>8,.0f}  @ ${pos.get('current_price',0):.2f}")
        lines.append("")

    lines += [
        "═" * 64,
        "  Execute manually in Fidelity. Not financial advice.",
        "═" * 64,
    ]
    return "\n".join(lines)