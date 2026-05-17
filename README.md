# Swing Signal Engine

Daily signal engine. Generates BUY/SELL/HOLD signals with AI grading, news sentiment, YouTube channel analysis, and options expected move data. Sends three daily reports by email and Telegram. No automated order execution — all trades are placed manually.

> **Intraday ORB trading** is handled by the separate [`trading-bot/`](../trading-bot) project.

---

## Daily workflow

| Time (ET) | Script | Purpose |
|---|---|---|
| 4:15 PM | `run_eod.py` | ★ Source of truth — technical signals for all symbols |
| 7:30 AM | `run_morning.py` | Overnight intelligence — news, gaps, YouTube, EM |
| 9:50 AM | `run_opening.py` | **Your trade list** — confirmed entries with levels |

---

## Accounts

| Account | Value | Type | Signal mode |
|---|---|---|---|
| Rollover IRA | $000 | ETFs | Full signals — min conviction 65 |
| Roth IRA | $000 | Stocks | Full signals — min conviction 70 (higher threshold, tax-free growth) |
| HSA | $000 | ETFs | Full signals — min conviction 65 |
| 401(k) | $000 | Mutual funds | Monitor only — no signals |

---

## EOD Report (4:15 PM)

The source of truth. Runs after market close on official closing prices.

**What it produces:**
- BUY/SELL/HOLD signal per symbol across all three tradeable accounts
- AI grading via Ollama (skips low-conviction HOLDs for speed — only actionable signals go through Ollama)
- Full technical scorecard per symbol: EMA 2/3/5, RSI(14), MACD, SMA50/200, ATR, volume ratio, 52-week range position, 1d/5d/20d price changes
- Portfolio dashboard: today's estimated P&L per account
- AI market narrative (Ollama-generated 4–6 sentence commentary)
- Concentration warnings: flags if a BUY signal would push a symbol past 10–15% of account
- Position sizing per account: conviction-tiered % of account × AI confidence multiplier
- Signals saved to `cache/signal_log.json` for morning reports to read

**Not the trade list** — the market is closed. These signals set up tomorrow's trades.

---

## Morning Report (7:30 AM)

Overnight intelligence before you execute anything.

**What it produces:**
- Pre-market SPY/QQQ moves and VIX level
- Options implied expected move for SPY and QQQ (ATM straddle × 0.68)
- AI-generated morning briefing (Ollama): overall market tone, what to watch at open
- YouTube channel analysis (see below)
- Today's watchlist: active EOD signals from last night with pre-market context and news sentiment
- Pre-market moves for your holdings (genuine pre-market quotes only — not closing prices)
- News sentiment per active-signal symbol (Yahoo Finance RSS + AI grading)
- Earnings alerts: symbols with earnings within 48h (BUY signals blocked)
- Full transcript appended at the bottom for each YouTube video

**Reading material** — trade confirmations arrive at 9:50 AM.

---

## Opening Report (9:50 AM) — Your Trade List

After the first 15 minutes of real price action have printed.

**For each active EOD signal:**

| Action | Meaning |
|---|---|
| **EXECUTE NOW** | Signal confirmed by opening action. Entry price, stop, and 2:1 target included. |
| **WAIT** | Signal present but not yet confirmed. Specific level to watch. |
| **STAND DOWN** | Opening action invalidates the signal. Skip today. |

Email subject line tells you immediately: `SWING SIGNAL: Opening 10:50 — EXECUTE: IBIT, PSLV`

---

## YouTube channel analysis

Monitors channels for new videos and extracts market signals.

For each video:
- Summary from video description (no AI required — instant)
- Bias from title keyword analysis
- Price levels extracted via regex: support, resistance, expected ranges, targets
- Expected move language detected: "752 to the upside, 725 to the downside"
- Portfolio cross-reference: which of your symbols were mentioned and whether they align or conflict with your EOD signals
- Full transcript appended to morning email (scrollable text box)
- Optional: set `ANTHROPIC_API_KEY` in `.env` for Claude-powered analysis (~$0.002/video, ~5s vs 30s+ with Ollama)

No API key required for basic operation. Uses YouTube RSS feed + `youtube-transcript-api`.

```bash
pip install youtube-transcript-api
```

---

## Options implied expected move

Fetched for SPY and QQQ each morning.

```
Expected Move = ATM straddle price × 0.68   (1 standard deviation)
```

Appears in the morning report alongside SPY/QQQ pre-market data:
```
OPTIONS IMPLIED EXPECTED MOVE
SPY  $583.20  daily EM ±$8.40 (1.4%)  lower $574.80  upper $591.60
QQQ  $500.10  daily EM ±$7.20 (1.4%)  lower $492.90  upper $507.30
              weekly EM ±$14.40       lower $485.70  upper $514.50
```

---

## Signal cross-reference

Conflicts are highlighted in both the EOD and morning reports:

```
SMH    HOLD  cv=42  ← aligned with FOM caution
NVDA   BUY   cv=83  ← CONFLICT — FOM says "wait, needs to consolidate"
GDE    HOLD  cv=47  ← aligned
```

---

## Symbols and portfolio

**`symbols.txt`** — your full watchlist, organized by account section. Signals are generated for all symbols whether or not you currently hold them (so you can get BUY signals on things you want to add).

```
# Rollover IRA (ETFs)
DBMF
SMH
...

# Roth IRA (stocks)
NVDA
AMAT
...
```

**`portfolio.json`** — current holdings (shares, avg cost, unrealized P&L). Used for position context only — not for signal generation.

**Update portfolio after a trade:**
```bash
python update_portfolio.py Portfolio_Positions_DATE.csv   # from Brokerage export
python update_portfolio.py                                # auto-finds latest CSV
python update_portfolio.py --dry-run                      # preview only
```

---

## Technical signals

Each symbol gets a full scorecard:

| Indicator | What's checked |
|---|---|
| EMA 2/3/5 | Crossover + alignment (bull/bear) |
| RSI(14) | Level + label (neutral/overbought/oversold) |
| MACD | Crossover + histogram direction |
| SMA 50/200 | Price position + distance % |
| Volume ratio | vs 20-day average |
| ATR(14) | Daily volatility % |
| 52-week range | Position as % of range |
| 1d/5d/20d | Price change % |
| Bull/bear score | Net score driving BUY/SELL/HOLD |

---

## Position sizing

Conviction-tiered, per account value:

| Conviction | Base % of account |
|---|---|
| 90+ | 7% |
| 80+ | 5% |
| 70+ | 3% |
| < 70 | 2% |

Multiplied by AI confidence (0.7× to 1.5×). Concentration warnings fire if a position would exceed 10–15% of account.

---

## Account rules

| Account | Min conviction | AI min confidence | Cooldown |
|---|---|---|---|
| Rollover IRA | 65 | 55% | 60 days |
| Roth IRA | 70 | 65% | 90 days |
| HSA | 65 | 55% | 60 days |

Sell cooldown prevents re-entering a position too soon after selling. Force-sell override bypasses cooldown when conviction ≥ 85 and AI confirms SELL.

---

## Setup

### Prerequisites

- Python 3.12
- [Ollama](https://ollama.com) — running locally (`qwen3:8b`)
- Gmail account with App Password (for email delivery)
- Brokerage account (for manual trade execution)

### Install

```bash
git clone https://github.com/virtualsheng/swing-signal-engine.git
cd swing_signal_engine

py -3.12 -m venv venv
venv\Scripts\activate

pip install pandas numpy python-dotenv yfinance requests
pip install youtube-transcript-api
```

### Configure

```bash
copy .env.template .env
# Fill in EMAIL_*, TELEGRAM_*, PORTFOLIO_VALUE
```

### Windows Task Scheduler — 3 tasks

| Time | Command |
|---|---|
| 4:15 PM | `python C:\path\run_eod.py` |
| 7:30 AM | `python C:\path\run_morning.py` |
| 9:50 AM | `python C:\path\run_opening.py` |

---

## Environment variables

```env
# Email (required for reports)
EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
EMAIL_RECIPIENT=you@example.com

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Anthropic API (optional — faster YouTube analysis ~$0.002/video)
ANTHROPIC_API_KEY=sk-ant-...

# Portfolio
PORTFOLIO_VALUE=753814
SWING_MIN_CONVICTION=65
SWING_FORCE_SELL_CONVICTION=85
```

---

## Project structure

```
swing_signal_engine/
├── run_eod.py              # ★ 4:15 PM — EOD signals
├── run_morning.py          # ★ 7:30 AM — morning intelligence
├── run_opening.py          # ★ 9:50 AM — trade list
├── update_portfolio.py     # Import positions from Fidelity CSV
│
├── signals/
│   ├── ai_engine.py        # Ollama: grading, regime, narrative
│   ├── data_fetcher.py     # Yahoo Finance daily bars + cache
│   ├── earnings_filter.py  # Earnings calendar (Yahoo Finance)
│   ├── expected_move.py    # Options implied expected move (SPY/QQQ)
│   ├── news_fetcher.py     # Yahoo Finance RSS headlines + sentiment
│   ├── opening_range.py    # 9:50 AM opening range confirmation
│   ├── portfolio.py        # Position sizing, cooldown, account config
│   ├── premarket_data.py   # Pre-market quotes, VIX, gap detection
│   ├── report_builder.py   # HTML + text report generation
│   ├── signal_engine.py    # Full technical scorecard per symbol
│   └── youtube_fetcher.py  # YouTube RSS + transcript + signal extraction
│
├── notifications/
│   └── notifier.py         # Email + Telegram delivery
│
├── cache/                  # Auto-generated, not committed
│   ├── signal_log.json     # Latest EOD signals (read by morning/opening)
│   ├── price_cache.json    # Daily bar cache
│   ├── news_cache.json     # News sentiment cache (2h TTL)
│   ├── youtube_cache.json  # Video analysis cache (per video ID)
│   └── sell_history.json   # Cooldown tracking per account:symbol
│
├── symbols.txt             # ★ Your watchlist by account
├── portfolio.json          # ★ Current holdings (update via update_portfolio.py)
├── .env                    # Credentials — never commit
├── .env.template
└── README.md
```

---

## All email subjects use the prefix `SWING SIGNAL:` for filtering

```
SWING SIGNAL: EOD 2026-05-16 — 12 signals for tomorrow
SWING SIGNAL: Morning Intel 2026-05-16 — SPY -1.1% | 12 signals to watch
SWING SIGNAL: Opening 2026-05-16 10:50 — EXECUTE: IBIT, PSLV
```

---

## Disclaimer

For educational and research purposes only. Not financial advice. Always verify signals before executing in Fidelity. Past performance does not guarantee future results.