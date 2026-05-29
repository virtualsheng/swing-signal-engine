# swing-signal-engine

> **v0.1.0** — Daily swing trading signal engine for retirement accounts. Generates BUY/SELL/HOLD signals across 4 accounts using technical analysis, AI grading, news sentiment, YouTube channel monitoring, and options expected move data. Sends reports via email, Telegram, and Discord. No automated order execution — all trades placed manually.

> Intraday ORB trading is handled by the companion [trading-bot](https://github.com/virtualsheng/trading-bot) project.

---

## Daily workflow

| Time (ET) | Script | Purpose |
|-----------|--------|---------|
| 4:15 PM | `run_eod.py` | ★ Source of truth — EOD signals for all symbols |
| 7:30 AM | `run_morning.py` | Pre-market intelligence — futures, news, YouTube, EM |
| 9:50 AM | `run_opening.py` | **Your trade list** — opening range confirmation with entry/stop/target |
| 3:50 PM | `run_prelim.py` | Pre-close SELL alert — 10-minute action window |

---

## Accounts

| Account | Type | Signal threshold |
|---------|------|-----------------|
| Rollover IRA | ETFs | Full signals — min conviction 65 |
| Roth IRA | Stocks | Full signals — min conviction 70 (higher bar, tax-free growth) |
| HSA | ETFs | Full signals — min conviction 65 |
| 401(k) | Mutual funds | Monitor only — no signals generated |

---

## EOD Report (4:15 PM) — `run_eod.py`

The source of truth. Runs on official closing prices after market close.

**What it produces:**
- BUY/SELL/HOLD signal per symbol across all tradeable accounts
- AI grading via Ollama/Gemini/Groq — confidence, reasoning, size multiplier
- Full technical scorecard per symbol: EMA 2/3/5, RSI(14), MACD, SMA50/200, ATR, vol ratio, 52-week range position, 1d/5d/20d price changes
- Portfolio dashboard: estimated P&L per account today
- AI market narrative: 4–6 sentence commentary on market regime and outlook
- Concentration warnings: flags if a BUY would push a symbol past 10–15% of account
- Position sizing: conviction-tiered % of account × AI confidence multiplier
- Signals saved to `cache/signal_log.json` for morning reports

**Not the trade list** — market is closed. These signals set up tomorrow's entries.

---

## Morning Report (7:30 AM) — `run_morning.py`

Overnight intelligence before you execute anything.

**What it produces:**
- Pre-market futures snapshot: DOW, S&P 500, Nasdaq, Oil, 10-yr yield, Gold, Silver, Bitcoin
- Options implied expected move for SPY and QQQ (ATM straddle × 0.68 = 1σ daily move)
- AI morning briefing: market tone and what to watch at open
- Today's watchlist: active EOD signals with pre-market gap context
- Pre-market price moves for current holdings (uses `ticker.history(prepost=True)` — `fast_info.pre_market_price` was silently returning None and has been removed)
- News sentiment per active-signal symbol (Yahoo Finance RSS + AI grading)
- Earnings alerts: BUY signals blocked for symbols with earnings within 48h
- YouTube channel analysis: new video summaries, bias, price levels, portfolio cross-reference
- Discord embed: structured mobile-friendly summary with futures, watchlist, top headline

---

## Opening Report (9:50 AM) — `run_opening.py`

Your trade list. Run after the first 15 minutes of price action.

For each active EOD signal:

| Verdict | Meaning |
|---------|---------|
| **EXECUTE NOW** | Opening range confirms the signal. Entry price, stop, and 2:1 target provided. Place a limit order. |
| **WAIT** | Signal present but opening action not yet confirming. Specific level to watch. Check again at noon. |
| **STAND DOWN** | Opening action invalidates the signal. Skip today entirely. |

Email subject: `SWING SIGNAL: Opening 09:50 — EXECUTE: IBIT, PSLV`

Discord BUY alert fires per EXECUTE NOW signal with full trade levels: entry, stop, target, R:R, suggested position size, RSI, vol ratio, SMA position.

---

## 3:50 PM Preliminary SELL Alert — `run_prelim.py`

Runs 10 minutes before market close. Fires only if SELL/STRONG_SELL signals are present.

- Near-close prices — same technical engine as EOD
- Does NOT overwrite `signal_log.json`
- No AI grading (too slow for 10-minute window)
- Flags signals that changed since yesterday's EOD
- Discord SELL embed fires immediately with conviction, price, RSI, intraday change

---

## Discord notifications

| Event | Time | Alert type |
|-------|------|-----------|
| Morning summary | 7:30 AM | Futures + watchlist embed — compact, mobile-readable |
| Confirmed BUY | 9:50 AM | Per EXECUTE NOW signal — entry, stop, target, R:R, size |
| SELL alert | 3:50 PM | Fires only if SELL signals present |
| EOD summary | 4:15 PM | All BUY/SELL signals for tomorrow + AI commentary |

All reports also delivered via email (HTML) and Telegram (plain text).

---

## Technical signal engine

8-indicator scorecard per symbol, producing a net conviction score (0–100).

| Indicator | Bullish condition |
|-----------|-----------------|
| EMA 2/3/5 alignment | EMA2 > EMA3 > EMA5 |
| EMA cross | Recent bull cross |
| RSI(14) | 45–65 range (extremes reduce conviction) |
| MACD crossover | MACD line above signal |
| MACD histogram | Positive and rising |
| SMA50 | Price above |
| SMA200 | Price above |
| Volume ratio | > 1.5× confirming, < 0.6× warning |

Signal thresholds:
- `STRONG_BUY`: net score ≥ 4, RSI 45–70, above both SMAs
- `BUY`: net score ≥ 2, RSI not extreme, sufficient volume
- `HOLD`: mixed signals
- `SELL` / `STRONG_SELL`: mirrored bear conditions

---

## AI stack

| Task | Provider | When |
|------|----------|------|
| Setup grading | Gemini → Groq → Ollama | EOD per symbol (cached across accounts) |
| Market narrative | Gemini → Groq → Ollama | EOD + Morning |
| Trade narrative | Gemini → Groq → Ollama | EOD per signal (batched 3 at a time) |
| Market regime | Gemini → Groq → Ollama | EOD |
| YouTube analysis | Ollama or Claude (optional) | Morning per new video |

All AI is additive — signals generate correctly without any AI configured. Ollama timeout reduced (25s), grade cache shared across accounts, narratives batched to prevent re-grading the same symbol.

---

## Position sizing

```
suggested_usd = account_value × conviction_tier × ai_confidence_multiplier
```

| Conviction tier | Allocation |
|----------------|-----------|
| STRONG (score ≥ 4.5) | 8–12% of account |
| High (score ≥ 3.5) | 5–8% |
| Moderate (score ≥ 2.5) | 3–5% |
| Low (score < 2.5) | 1–3% |

Entry blocked if: cooldown active (60 days after SELL), near earnings (48h), concentration limit exceeded.

---

## Portfolio management

`portfolio.json` — current positions with shares, avg cost, unrealized P&L. Updated automatically by `python update_portfolio.py` with a CSV export. `auto_update_portfolio.py` auto-detects and applies any newer CSV on startup.

Portfolio values are computed live from `shares × today's live price` rather than stale stored values.

---

## Environment variables

```env
# Email
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_16_char_gmail_app_password
EMAIL_RECIPIENT=you@example.com

# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=987654321

# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# AI (all optional — graceful fallback without)
GEMINI_API_KEY=AIza...          # aistudio.google.com — free
GROQ_API_KEY=gsk_...            # console.groq.com — free
OLLAMA_MODEL=qwen3:4b           # local fallback
ANTHROPIC_API_KEY=sk-ant-...    # optional: Claude for YouTube analysis

# Signal thresholds
SWING_MIN_CONVICTION=65
SWING_FORCE_SELL_CONVICTION=85
SWING_COOLDOWN_DAYS=60
```

---

## Project structure

```
swing_signal_engine/
├── run_eod.py              # ★ 4:15 PM — EOD signals
├── run_morning.py          # ★ 7:30 AM — pre-market intelligence
├── run_opening.py          # ★ 9:50 AM — opening confirmation + trade list
├── run_prelim.py           # ★ 3:50 PM — pre-close SELL alert
├── run_signals.py          # Account-aware signal runner (EOD + pre-market modes)
├── update_portfolio.py     # Import from CSV export
│
├── signals/
│   ├── signal_engine.py    # 8-indicator technical scorecard
│   ├── ai_engine.py        # Gemini/Groq/Ollama grading + narratives
│   ├── data_fetcher.py     # Yahoo Finance daily bars + 2h cache
│   ├── market_futures.py   # Futures snapshot (history() — not fast_info)
│   ├── opening_range.py    # 9:50 AM ORB confirmation
│   ├── portfolio.py        # Sizing, cooldown, account config
│   ├── report_builder.py   # HTML + plain-text report generation
│   ├── news_fetcher.py     # Yahoo Finance RSS + AI sentiment
│   ├── earnings_filter.py  # Earnings calendar (blocks BUY signals)
│   ├── expected_move.py    # Options ATM straddle (SPY/QQQ)
│   ├── premarket_data.py   # Pre-market quotes (history prepost=True)
│   └── youtube_fetcher.py  # YouTube RSS + transcript + signal extraction
│
├── notifications/
│   ├── notifier.py         # Email + Telegram + Discord delivery
│   └── discord.py          # Rich embeds: morning, BUY, SELL, EOD
│
├── symbols.txt             # ★ Watchlist by account section
├── portfolio.json          # ★ Current holdings
├── .env                    # Credentials — never commit
└── .env.template
```

---

## Schedule setup (Windows Task Scheduler)

```
4:15 PM  →  python run_eod.py
7:30 AM  →  python run_morning.py
9:50 AM  →  python run_opening.py
3:50 PM  →  python run_prelim.py
```

---

## Email subject format

All emails use `SWING SIGNAL:` prefix for easy inbox filtering:

```
SWING SIGNAL: EOD 2026-05-29 — 8 actions
SWING SIGNAL: Morning Intel 2026-05-29 — SPY +0.7% | 6 signals to watch
SWING SIGNAL: Opening 2026-05-29 09:50 — EXECUTE: IBIT, SMH
SWING SIGNAL: PRELIM 3:50 PM — ACT NOW: NVDA | WATCH: AMAT
```

---

## Disclaimer

For educational and research purposes only. Not financial advice. Always verify signals before executing. Past performance does not guarantee future results.