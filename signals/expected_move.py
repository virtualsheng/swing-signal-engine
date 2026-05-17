"""
expected_move.py — Options-implied expected move for SPY/QQQ
──────────────────────────────────────────────────────────────
Calculates the market's own expectation for price range using
the ATM straddle price from the options chain.

Formula: Expected Move = ATM Straddle Price × 0.68
  - ATM straddle = ATM call mid + ATM put mid
  - ×0.68 converts from 1 standard deviation to ~68% probability range
  - This is exactly what options market makers use for weekly expected moves
  - Matches what The Stocks Channel and other analysts quote as "expected move"

Returns for each symbol:
  {
    "symbol":        str,
    "price":         float,
    "expiry":        str,       # nearest weekly expiration
    "expected_move": float,     # ±$ amount (1 SD)
    "em_pct":        float,     # ±% amount
    "upper":         float,     # price + expected_move
    "lower":         float,     # price - expected_move
    "atm_iv":        float,     # ATM implied volatility
    "straddle":      float,     # raw straddle price
    "weekly_upper":  float,     # end-of-week expiry upper
    "weekly_lower":  float,     # end-of-week expiry lower
  }

Free — uses yfinance options chain, no API key needed.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_expected_move(symbol: str) -> dict | None:
    """
    Calculate ATM straddle-based expected move for a symbol.
    Uses the nearest weekly expiration (typically Friday).
    Returns None if options data unavailable.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)

        # Get current price
        hist  = ticker.history(period="2d", interval="1d")
        if hist is None or hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])

        # Get available expirations
        expirations = ticker.options
        if not expirations:
            return None

        # Find nearest expiration (daily or weekly)
        nearest = expirations[0]

        # Also find the next Friday for weekly expected move
        today      = datetime.now()
        days_ahead = (4 - today.weekday()) % 7  # days until Friday
        if days_ahead == 0:
            days_ahead = 7
        next_friday = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # Use nearest expiry for daily, next Friday for weekly
        weekly_expiry = next((e for e in expirations if e >= next_friday), nearest)

        def _calc_em(expiry: str) -> dict | None:
            try:
                chain = ticker.option_chain(expiry)
                calls = chain.calls
                puts  = chain.puts

                if calls.empty or puts.empty:
                    return None

                # Find ATM strike
                atm_idx    = (calls["strike"] - price).abs().argsort().iloc[0]
                atm_strike = float(calls.iloc[atm_idx]["strike"])

                atm_call_rows = calls[calls["strike"] == atm_strike]
                atm_put_rows  = puts[puts["strike"]  == atm_strike]

                if atm_call_rows.empty or atm_put_rows.empty:
                    return None

                atm_call = atm_call_rows.iloc[0]
                atm_put  = atm_put_rows.iloc[0]

                # Use mid price; fall back to last price if bid/ask spread is 0
                call_bid, call_ask = float(atm_call["bid"]), float(atm_call["ask"])
                put_bid,  put_ask  = float(atm_put["bid"]),  float(atm_put["ask"])

                call_mid = (call_bid + call_ask) / 2 if call_ask > 0 else float(atm_call.get("lastPrice", 0))
                put_mid  = (put_bid  + put_ask)  / 2 if put_ask  > 0 else float(atm_put.get("lastPrice",  0))

                if call_mid <= 0 or put_mid <= 0:
                    return None

                straddle = call_mid + put_mid
                em       = straddle * 0.68
                em_pct   = em / price * 100 if price > 0 else 0
                atm_iv   = float(atm_call.get("impliedVolatility", 0))

                return {
                    "expiry":   expiry,
                    "straddle": round(straddle, 2),
                    "em":       round(em, 2),
                    "em_pct":   round(em_pct, 2),
                    "upper":    round(price + em, 2),
                    "lower":    round(price - em, 2),
                    "atm_iv":   round(atm_iv, 4),
                    "strike":   atm_strike,
                }
            except Exception as e:
                logger.debug(f"Options chain error for {symbol} {expiry}: {e}")
                return None

        # Calculate for nearest expiry (daily/short-term)
        daily_em = _calc_em(nearest)
        if not daily_em:
            return None

        # Calculate for weekly expiry
        weekly_em = _calc_em(weekly_expiry) if weekly_expiry != nearest else daily_em

        result = {
            "symbol":        symbol,
            "price":         round(price, 2),
            "expiry":        daily_em["expiry"],
            "expected_move": daily_em["em"],
            "em_pct":        daily_em["em_pct"],
            "upper":         daily_em["upper"],
            "lower":         daily_em["lower"],
            "atm_iv":        daily_em["atm_iv"],
            "straddle":      daily_em["straddle"],
        }

        if weekly_em and weekly_em["expiry"] != daily_em["expiry"]:
            result["weekly_expiry"] = weekly_em["expiry"]
            result["weekly_upper"]  = weekly_em["upper"]
            result["weekly_lower"]  = weekly_em["lower"]
            result["weekly_em"]     = weekly_em["em"]
            result["weekly_em_pct"] = weekly_em["em_pct"]
        else:
            result["weekly_expiry"] = daily_em["expiry"]
            result["weekly_upper"]  = daily_em["upper"]
            result["weekly_lower"]  = daily_em["lower"]
            result["weekly_em"]     = daily_em["em"]
            result["weekly_em_pct"] = daily_em["em_pct"]

        return result

    except Exception as e:
        logger.warning(f"{symbol}: expected move failed — {e}")
        return None


def get_market_expected_moves() -> dict:
    """
    Get expected moves for SPY and QQQ.
    Returns {symbol: result_dict}.
    Called once per morning report — takes ~5 seconds.
    """
    results = {}
    for sym in ["SPY", "QQQ"]:
        em = get_expected_move(sym)
        if em:
            results[sym] = em
            logger.info(
                f"  {sym} expected move: "
                f"±${em['expected_move']:.2f} ({em['em_pct']:.1f}%) | "
                f"Range: ${em['lower']:.2f}–${em['upper']:.2f} "
                f"[{em['expiry']}]"
            )
            if em.get("weekly_em") and em.get("weekly_expiry") != em["expiry"]:
                logger.info(
                    f"  {sym} weekly move:    "
                    f"±${em['weekly_em']:.2f} ({em['weekly_em_pct']:.1f}%) | "
                    f"Range: ${em['weekly_lower']:.2f}–${em['weekly_upper']:.2f} "
                    f"[{em['weekly_expiry']}]"
                )
        else:
            logger.debug(f"  {sym}: expected move unavailable (market closed or no options data)")
    return results


def format_em_html(em_data: dict) -> str:
    """
    Format expected move data as an HTML panel row.
    Used in morning report market overview section.
    """
    if not em_data:
        return ""

    rows = ""
    for sym, em in em_data.items():
        weekly = em.get("weekly_em") and em.get("weekly_expiry") != em["expiry"]
        rows += f"""
        <tr style="border-top:0.5px solid #E8E6DF">
          <td style="padding:6px 8px;font-weight:500">{sym}</td>
          <td style="padding:6px 8px">${em['price']:.2f}</td>
          <td style="padding:6px 8px;color:#BA7517">
            ±${em['expected_move']:.2f} <span style="color:#888;font-size:11px">({em['em_pct']:.1f}%)</span>
          </td>
          <td style="padding:6px 8px;color:#E24B4A">${em['lower']:.2f}</td>
          <td style="padding:6px 8px;color:#1D9E75">${em['upper']:.2f}</td>
          <td style="padding:6px 8px;font-size:11px;color:#888">{em['expiry']}</td>
          {"" if not weekly else f'''
          <td style="padding:6px 8px;color:#BA7517;font-size:11px">
            Weekly: ${em['weekly_lower']:.2f}–${em['weekly_upper']:.2f}
            <span style="color:#888">(±${em['weekly_em']:.2f})</span>
          </td>'''}
        </tr>"""

    return f"""
    <div style="margin-bottom:20px;border:0.5px solid #D3D1C7;border-radius:8px;overflow:hidden">
      <div style="background:#F1EFE8;padding:10px 14px;border-bottom:0.5px solid #D3D1C7">
        <span style="font-weight:500;font-size:14px">Options Implied Expected Move</span>
        <span style="color:#888;font-size:12px;margin-left:8px">
          ATM straddle × 0.68 — market's own probability range
        </span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#F7F5EE">
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Symbol</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Price</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Daily EM</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Lower</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Upper</th>
          <th style="padding:6px 8px;font-weight:400;color:#5F5E5A;font-size:12px">Expiry</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


def format_em_text(em_data: dict) -> str:
    """Format expected move for plain text report."""
    if not em_data:
        return ""
    lines = ["  OPTIONS IMPLIED EXPECTED MOVE:"]
    for sym, em in em_data.items():
        lines.append(
            f"    {sym:4}  price=${em['price']:.2f}  "
            f"daily EM=±${em['expected_move']:.2f} ({em['em_pct']:.1f}%)  "
            f"range=${em['lower']:.2f}–${em['upper']:.2f}"
        )
        if em.get("weekly_em") and em.get("weekly_expiry") != em["expiry"]:
            lines.append(
                f"          weekly EM=±${em['weekly_em']:.2f} ({em['weekly_em_pct']:.1f}%)  "
                f"range=${em['weekly_lower']:.2f}–${em['weekly_upper']:.2f}"
                f"  [{em['weekly_expiry']}]"
            )
    return "\n".join(lines)