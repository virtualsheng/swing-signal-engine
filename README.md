# Swing Signal Engine
### Daily BUY/SELL/HOLD signals for Fidelity retirement accounts

No automated trading. No broker connection. Signals only — you execute manually in Fidelity.

---

## What it does

Runs twice daily (8:30 AM pre-market + 4:15 PM EOD) and delivers a report like this to your email and Telegram:

```
═══════════════════════════════════════════════════════════
  SWING SIGNALS — EOD — May 16, 2026 16:15 ET
  Portfolio: $750,000
═══════════════════════════════════════════════════════════

  MARKET REGIME: 📈 TRENDING_UP
  Bias: BULLISH — SPY making higher highs on increasing volume

  ┌─ ACTION REQUIRED ──────────────────────────────────────
  │  SYM    CONVICTION BAR   SCORE  SIGNAL       PRICE       SIZE
  │
  │  🟢 SMH    ████████░░   82  STRONG_BUY   $237.50  →  $37,500 (5.0%)
  │  🟢 NVDA   ███████░░░   74  BUY          $892.10  →  $22,500 (3.0%)
  │  🔴 URA    ████░░░░░░   41  SELL         $29.80   →  reduce position
  └────────────────────────────────────────────────────────

  ┌─ WATCHING ─────────────────────────────────────────────
  │  🟡 IBIT   ██████░░░░   60  HOLD         $58.20
  │  🟡 PLTR   █████░░░░░   55  HOLD         $84.10
  └────────────────────────────────────────────────────────
```

Each signal includes:
- Technical conviction score (EMA, RSI, MACD, SMA50/200, volume)
- AI confidence grade from local Ollama (qwen3:8b)
- Suggested position size in $ and % of portfolio
- 2-sentence AI narrative explaining the setup
- Earnings filter (blocks BUY within 48h of earnings)
- Sell cooldown (won't flip BUY→SELL within 60 days to avoid whipsaws)

---

## Setup

### 1. Install dependencies
```bash
pip install yfinance pandas numpy requests python-dotenv
```

### 2. Configure .env
```bash
cp .env.template .env
# Edit .env with your Gmail app password, Telegram bot token, portfolio value
```

### 3. Set up symbols.txt
Add one symbol per line — your Fidelity watchlist:
```
# Retirement account watchlist
QQQ
SMH
NVDA
IBIT
URA
...
```

### 4. Update portfolio.json
Edit `portfolio.json` with your current holdings:
```json
{
  "portfolio_value": 750000,
  "positions": {
    "SMH":  {"shares": 100, "avg_cost": 220.50, "date_entered": "2026-03-15"},
    "NVDA": {"shares":  50, "avg_cost": 890.00, "date_entered": "2026-01-10"}
  }
}
```

### 5. Start Ollama (for AI grading)
```bash
ollama serve
# In another terminal:
ollama pull qwen3:8b
```
The engine falls back gracefully if Ollama is unavailable.

### 6. Test run
```bash
python run_signals.py eod
```

---

## Windows Task Scheduler setup

Run twice daily automatically:

**Task 1 — Pre-market (8:30 AM ET)**
- Program: `C:\Python312\python.exe`
- Arguments: `C:\Users\sheng\Documents\swing_signal_engine\run_signals.py premarket`
- Start in: `C:\Users\sheng\Documents\swing_signal_engine`
- Trigger: Daily at 8:30 AM, repeat Mon–Fri only

**Task 2 — EOD (4:15 PM ET)**
- Program: `C:\Python312\python.exe`
- Arguments: `C:\Users\sheng\Documents\swing_signal_engine\run_signals.py eod`
- Start in: `C:\Users\sheng\Documents\swing_signal_engine`
- Trigger: Daily at 4:15 PM, repeat Mon–Fri only

---

## Workflow

1. **8:30 AM** — Pre-market report arrives in email + Telegram
2. **Review signals** — check BUY/SELL recommendations
3. **Execute in Fidelity** — place orders manually (full control)
4. **Update portfolio.json** — add/remove positions after each trade
5. **4:15 PM** — EOD report with updated signals using official close prices

After executing a SELL in Fidelity:
- Remove the position from `portfolio.json`
- The engine automatically records the sell date for cooldown tracking

---

## Signal logic

### Technical (same as ORB bot)
| Indicator | Weight | Signal triggers |
|---|---|---|
| EMA 2/3/5 crossover | 2 | Bullish/bearish alignment |
| RSI(14) | 1 | Oversold <35 (bull) / overbought >65 (bear) |
| MACD crossover | 1 | Signal line cross |
| SMA50 | 1 | Price above/below |
| SMA200 | 1 | Price above/below |
| Volume ratio | 0.5 | >1.5x average confirms direction |

### AI grading (Ollama qwen3:8b)
- Grades setup 0.0–1.0 confidence for retirement account context
- Generates 2-sentence narrative per signal
- Suggests size multiplier (0.5x–2.0x base position)

### Position sizing
| Conviction | Base % | Example on $750k |
|---|---|---|
| 90+ | 7% | $52,500 |
| 80–89 | 5% | $37,500 |
| 70–79 | 3% | $22,500 |
| <70 | 2% | $15,000 |

AI confidence multiplies the base size (0.7x–1.5x).

### Filters
- **Earnings filter**: blocks BUY within 48h of earnings report
- **Sell cooldown**: 60-day minimum between SELL signals per symbol
- **Force-sell override**: bypasses cooldown if conviction ≥ 85 on strong SELL
- **Minimum conviction**: configurable via SWING_MIN_CONVICTION in .env

---

## Files

```
swing_signal_engine/
  run_signals.py              # main runner
  portfolio.json              # your current holdings (update manually)
  symbols.txt                 # your watchlist
  .env                        # credentials (never commit)
  .env.template               # template
  signals/
    signal_engine.py          # technical indicators
    ai_engine.py              # Ollama grading + narrative
    data_fetcher.py           # Yahoo Finance + caching
    earnings_filter.py        # earnings calendar
    portfolio.py              # portfolio state + sizing
    report_builder.py         # text + HTML report formatting
  notifications/
    notifier.py               # email + Telegram delivery
  cache/
    price_cache.json          # daily bar cache (auto)
    sell_history.json         # sell dates for cooldown (auto)
    signal_log.json           # latest signal per symbol (auto)
  logs/
    signals_YYYYMMDD_HHMMSS.log
```

---

## Tax advantages

Running this in Fidelity Rollover IRA + Roth IRA means:
- **No capital gains tax** on trades inside the accounts
- **No wash sale rules** apply
- **Roth IRA**: tax-free growth + withdrawals in retirement
- **Rollover IRA**: tax-deferred growth

The 60-day sell cooldown also naturally encourages holding positions long enough to benefit from long-term treatment in taxable accounts if you ever run this on a brokerage account.
