"""
signals/youtube_fetcher.py — YouTube analysis for morning report
──────────────────────────────────────────────────────────────────
Pipeline per channel:
  1. Fetch latest video via YouTube RSS (no API key needed)
  2. Download full transcript via youtube-transcript-api
  3. Regex-scan for ticker mentions + price levels
  4. AI summary via llm_router (Gemini → Groq → Ollama)
     Produces: overall_bias, key_points (3-5 specific insights),
               symbols_mentioned with sentiment, price targets
  5. Fallback to description-only summary if AI unavailable
  6. Cross-reference symbols against your EOD signals

Install: pip install youtube-transcript-api
"""

import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_router import llm_call, llm_call_text

logger = logging.getLogger(__name__)

CHANNELS = [
    {
        "channel_id":   "UCfdPOTevbfCh_QHsyPeZ8MQ",
        "name":         "Figuring Out Money",
        "handle":       "@FiguringOutMoney",
        "max_age_days": 3,
    },
    {
        "channel_id":   "UCUP_ao_7-Yct5FcVIA4Kobg",
        "name":         "The Stocks Channel",
        "handle":       "@thestockschannel",
        "max_age_days": 7,
    },
]

CACHE_FILE    = os.path.join(os.path.dirname(__file__), "..", "cache", "youtube_cache.json")
FETCH_TIMEOUT = 10

COMMON_TICKERS = {
    "QQQ","SPY","TQQQ","SQQQ","NVDA","SMH","AAPL","MSFT","AMZN","GOOGL",
    "META","TSLA","AMD","INTC","TSM","AVGO","MU","LRCX","AMAT","PLTR",
    "GLD","GLDM","GDE","PSLV","IBIT","DBC","DBMF","VUG","AVUV","VLUE",
    "RKLB","ARIS","GEV","JPM","PAAS","AG","SNDK","REMX","EWT","EWY",
    "EWJV","GRID","NANR","SPMO","UFO","URA","DRAM","QQQM","SLVP","URNM",
    "XLY","XLK","XLF","XLE","XLC","TLT","IWM","DIA","VXX","UVXY",
    "BTC","ETH","COIN","MSTR",
}

BULL_WORDS = [
    "support","buy","bullish","long","breakout","higher","upside","bounce",
    "rally","strength","accumulate","holding","above","ripping","surge",
    "target","resistance flipped","momentum","trending up","buy signal",
]
BEAR_WORDS = [
    "resistance","sell","bearish","short","breakdown","lower","downside",
    "weak","distribution","caution","warning","breaking","below","failed",
    "collapse","diverge","divergence","declining","danger","beneath",
    "sell signal","top","reversal","head and shoulders",
]

FILLER_PHRASES = {
    "subscribe", "like and subscribe", "like this video", "welcome back",
    "make sure to", "hit the bell", "notification", "comment below",
    "patreon", "discord", "in today's video", "let's get into",
    "we do reports", "monday wednesday",
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# ── RSS ───────────────────────────────────────────────────────────────────────

def get_latest_videos(channel_id: str, max_results: int = 5) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            logger.warning(f"RSS {channel_id}: HTTP {resp.status_code}")
            return []
        ns = {
            "atom":  "http://www.w3.org/2005/Atom",
            "yt":    "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }
        root    = ET.fromstring(resp.text)
        videos  = []
        for entry in root.findall("atom:entry", ns)[:max_results]:
            video_id = entry.findtext("yt:videoId", "", ns)
            title    = entry.findtext("atom:title", "", ns)
            pub_raw  = entry.findtext("atom:published", "", ns)
            mg       = entry.find("media:group", ns)
            desc     = (mg.findtext("media:description", "", ns) if mg else "") or ""
            if not video_id:
                continue
            pub_dt = None
            if pub_raw:
                try:
                    pub_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                except Exception:
                    pass
            age = (datetime.now(pub_dt.tzinfo) - pub_dt).days if pub_dt else 99
            videos.append({
                "video_id":    video_id,
                "title":       title,
                "description": desc[:1500],
                "published":   pub_dt.strftime("%Y-%m-%d") if pub_dt else "",
                "url":         f"https://www.youtube.com/watch?v={video_id}",
                "age_days":    age,
            })
        return videos
    except Exception as e:
        logger.warning(f"RSS fetch failed for {channel_id}: {e}")
        return []


# ── Transcript ────────────────────────────────────────────────────────────────

def get_full_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        fetched = YouTubeTranscriptApi().fetch(video_id)
        text = " ".join(s.text for s in fetched)
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Strip opening filler
        sentences = re.split(r"(?<=[.!?])\s+", text)
        clean = [s for i, s in enumerate(sentences)
                 if not (i < 3 and any(s.lower().strip().startswith(f)
                                        for f in FILLER_PHRASES))]
        return " ".join(clean)
    except ImportError:
        logger.error("pip install youtube-transcript-api")
        return None
    except Exception as e:
        logger.debug(f"Transcript unavailable for {video_id}: {e}")
        return None


# ── Bias detection ────────────────────────────────────────────────────────────

def _bias_from_text(text: str) -> str:
    t    = text.lower()
    bull = sum(1 for w in BULL_WORDS if w in t)
    bear = sum(1 for w in BEAR_WORDS if w in t)
    if   bull > bear * 1.5: return "bullish"
    elif bear > bull * 1.5: return "bearish"
    elif bear > bull:       return "mixed"
    else:                   return "neutral"


# ── Ticker extraction ─────────────────────────────────────────────────────────

def _extract_tickers(text: str, portfolio_symbols: list[str]) -> list[dict]:
    all_tickers = COMMON_TICKERS | set(portfolio_symbols)
    results = []
    seen = set()
    for ticker in all_tickers:
        if not re.search(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE):
            continue
        if ticker.upper() in seen:
            continue
        seen.add(ticker.upper())
        windows = ""
        for m in re.finditer(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE):
            windows += " " + text[max(0, m.start()-80):min(len(text), m.end()+80)]
        wl   = windows.lower()
        bull = sum(1 for w in BULL_WORDS if w in wl)
        bear = sum(1 for w in BEAR_WORDS if w in wl)
        sent = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
        action = "none"
        for act in ["buy", "sell", "hold", "wait", "watch", "avoid"]:
            if act in wl:
                action = act
                break
        results.append({
            "symbol":           ticker.upper(),
            "sentiment":        sent,
            "action_mentioned": action,
        })
    return results


def _extract_price_levels(text: str) -> list[dict]:
    results = []
    # Find patterns like "S&P at 5300", "support at 540", "target 560"
    patterns = [
        (r"(?:support|floor|bottom)\s+(?:at|near|around)?\s*\$?(\d{2,5}(?:\.\d{1,2})?)", "support"),
        (r"(?:resistance|ceiling|top)\s+(?:at|near|around)?\s*\$?(\d{2,5}(?:\.\d{1,2})?)", "resistance"),
        (r"(?:target|objective)\s+(?:of|at|is)?\s*\$?(\d{2,5}(?:\.\d{1,2})?)", "target"),
        (r"gamma\s+flip\s+(?:at|near|around)?\s*\$?(\d{2,5}(?:\.\d{1,2})?)", "gamma_flip"),
    ]
    for pattern, level_type in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            val = float(m.group(1))
            if 10 < val < 100000:
                results.append({"level_type": level_type, "value": val,
                                 "context": text[max(0,m.start()-40):m.end()+40]})
    return results[:6]


# ── Key sentence extraction ───────────────────────────────────────────────────

def _extract_key_sentences(transcript: str, max_words: int = 400) -> str:
    """Score sentences by information density, return top ones as digest."""
    KEY_TERMS = {
        "support","resistance","target","breakout","breakdown","yield","inflation",
        "recession","earnings","sector","weekly","monthly","expected","range",
        "consumer","discretionary","technology","energy","rally","selloff",
        "divergence","breadth","momentum","level","critical","key","watch",
        "important","warning","gamma","flip","squeeze","oversold","overbought",
    }
    sentences = re.split(r"(?<=[.!?])\s+", transcript)
    scored = []
    for i, sent in enumerate(sentences):
        if len(sent.split()) < 8:
            continue
        s_lower = sent.lower()
        if any(f in s_lower for f in FILLER_PHRASES):
            continue
        score = 0
        score += len(re.findall(r"\$?\d{2,5}(?:\.\d{1,2})?(?:%|\s*percent)?", sent)) * 4
        score += sum(2 for t in COMMON_TICKERS
                     if re.search(r"\b" + re.escape(t) + r"\b", sent, re.IGNORECASE))
        score += sum(1 for t in KEY_TERMS if t in s_lower)
        score += min(4, len(sent.split()) // 8)
        scored.append((score, i, sent))
    scored.sort(key=lambda x: -x[0])
    selected = []
    word_count = 0
    for score, idx, sent in scored:
        if score <= 0:
            continue
        w = len(sent.split())
        if word_count + w > max_words:
            continue
        selected.append((idx, sent))
        word_count += w
        if len(selected) >= 15:
            break
    selected.sort(key=lambda x: x[0])
    return " ".join(s for _, s in selected)


# ── Description fallback ──────────────────────────────────────────────────────

def _description_summary(title: str, description: str) -> dict:
    bias = _bias_from_text(title + " " + title)
    if bias == "neutral" and description:
        bias = _bias_from_text(description[:300])
    SKIP = {"subscribe","discord","patreon","tradingview","http","►","▶","👉","📌"}
    paras = [p.strip() for p in description.split("\n")
             if len(p.strip()) >= 40 and not any(s in p.lower() for s in SKIP)]
    summary = paras[0][:500] if paras else title
    key_points = []
    for para in paras[1:5]:
        clean = re.sub(r"^[-•▸►*]\s+", "", para).strip()[:150]
        if clean and clean not in summary[:100]:
            key_points.append(clean)
        if len(key_points) >= 3:
            break
    return {
        "summary":      summary,
        "overall_bias": bias,
        "week_outlook": "",
        "key_points":   key_points or [title],
        "via":          "description",
    }


# ── AI analysis ───────────────────────────────────────────────────────────────

def _ai_summarize(title: str, description: str, transcript: str,
                   channel_name: str) -> dict | None:
    """
    Use AI to produce a rich summary of the video.
    Returns structured dict or None if all providers fail.
    """
    # Use key sentences from transcript if available, else description
    if transcript and len(transcript.split()) > 100:
        content = _extract_key_sentences(transcript, max_words=600)
        source_note = f"Transcript ({len(transcript.split())} words, key sentences extracted)"
    elif transcript:
        content = transcript[:1200]
        source_note = "Full transcript (short video)"
    else:
        content = description[:1000]
        source_note = "Video description (no transcript available)"

    prompt = f"""Analyze this stock market video and provide a detailed summary for a swing trader.
Respond ONLY with valid JSON — no markdown, no preamble.

Channel: {channel_name}
Title: {title}
Source: {source_note}
Content:
{content}

Return this exact JSON structure:
{{
  "summary": "<3-4 sentences capturing the MAIN thesis, specific levels mentioned, and key takeaway — be specific, not generic>",
  "overall_bias": "<bullish|bearish|neutral|mixed>",
  "week_outlook": "<1 specific sentence about near-term market direction with levels if mentioned>",
  "key_points": [
    "<specific insight 1 — include numbers/tickers/levels if mentioned>",
    "<specific insight 2>",
    "<specific insight 3>",
    "<specific insight 4 if content warrants>",
    "<specific insight 5 if content warrants>"
  ],
  "important_levels": [
    "<e.g. SPY support at 520, QQQ resistance at 450>",
    "<another level if mentioned>"
  ],
  "tickers_discussed": ["<TICKER1>", "<TICKER2>"],
  "ticker_sentiment": {{
    "<TICKER1>": "<bullish|bearish|neutral>",
    "<TICKER2>": "<bullish|bearish|neutral>"
  }},
  "action_for_viewer": "<1 sentence: what should the viewer do or watch for based on this video>"
}}

Critical rules:
- summary MUST be specific to THIS video — mention the actual thesis, not generic platitudes
- Include specific price levels, percentages, or technical levels if mentioned
- key_points should each be distinct actionable insights, not rewordings of the summary
- If the title mentions a specific level (e.g. "7300 gamma flip"), it MUST appear in summary/key_points
- tickers_discussed: only tickers explicitly named in the content"""

    result = llm_call(prompt, expect_json=True, timeout=50, tag="youtube/ai_summary")

    if not result or not isinstance(result, dict):
        return None
    if not result.get("summary") or len(result["summary"]) < 30:
        return None
    # Validate key_points is a list
    if not isinstance(result.get("key_points"), list):
        result["key_points"] = []
    result["via"] = "ai"
    return result


# ── Cross-reference ───────────────────────────────────────────────────────────

def cross_reference(yt_symbols: list[dict], signal_log: dict) -> list[dict]:
    results = []
    for sym_data in yt_symbols:
        sym = sym_data.get("symbol", "")
        if not sym:
            continue
        for key, val in signal_log.items():
            if key.endswith(f":{sym}"):
                sig     = val.get("signal", "")
                account = key.split(":")[0]
                yt_sent = sym_data.get("sentiment", "neutral")
                eod_bull = sig in ("BUY", "STRONG_BUY")
                yt_bull  = yt_sent == "bullish"
                eod_bear = sig in ("SELL", "STRONG_SELL")
                yt_bear  = yt_sent == "bearish"
                alignment = (
                    "aligned" if (eod_bull and yt_bull) or (eod_bear and yt_bear) else
                    "opposed" if (eod_bull and yt_bear) or (eod_bear and yt_bull) else
                    "neutral"
                )
                results.append({
                    "symbol":      sym,
                    "fom_view":    yt_sent,
                    "action":      sym_data.get("action_mentioned", "none"),
                    "your_signal": sig,
                    "account":     account,
                    "alignment":   alignment,
                })
                break
    return results


# ── Main per-channel fetch ────────────────────────────────────────────────────

def fetch_channel_analysis(
    channel: dict,
    portfolio_symbols: list[str],
    signal_log: dict,
    force: bool = False,
) -> dict | None:
    channel_id   = channel["channel_id"]
    channel_name = channel["name"]
    max_age      = channel["max_age_days"]
    cache        = _load_cache()

    videos = get_latest_videos(channel_id)

    # RSS fallback
    if not videos:
        logger.warning(f"{channel_name}: RSS unavailable — checking cache")
        best = None
        best_date = ""
        for key, val in cache.items():
            if key.startswith(f"{channel_id}:") and val.get("published","") > best_date:
                best_date = val["published"]
                best = val
        if best:
            logger.warning(f"{channel_name}: using cached ({best_date}) — may not be latest")
            result = dict(best)
            result["_stale_cache"] = True
            result["cross_reference"] = cross_reference(
                result.get("symbols_mentioned", []), signal_log)
            return result
        return None

    target = next((v for v in videos if v["age_days"] <= max_age), None)
    if not target:
        logger.info(
            f"{channel_name}: no video within {max_age}d "
            f"(latest: {videos[0]['title'][:40]} — {videos[0]['age_days']}d ago)"
        )
        return None

    video_id  = target["video_id"]
    cache_key = f"{channel_id}:{video_id}"

    logger.info(
        f"{channel_name}: target → '{target['title'][:55]}' "
        f"({target['published']}, {target['age_days']}d ago)"
    )

    # Cache check
    if not force and cache_key in cache:
        cached = cache[cache_key]
        is_bad = not cached.get("summary") or len(cached.get("summary","")) < 20
        if not is_bad:
            logger.info(f"{channel_name}: cache hit — {video_id}")
            result = dict(cached)
            result["cross_reference"] = cross_reference(
                result.get("symbols_mentioned", []), signal_log)
            return result
        logger.info(f"{channel_name}: re-analyzing (cached result was incomplete)")

    # Fetch transcript
    logger.info(f"{channel_name}: fetching transcript for {video_id}...")
    transcript = get_full_transcript(video_id) or ""
    if transcript:
        logger.info(f"{channel_name}: {len(transcript.split())} words — analyzing...")
    else:
        logger.info(f"{channel_name}: no transcript — using description")

    # AI analysis
    logger.info(f"{channel_name}: AI analysis (Gemini → Groq → Ollama)...")
    ai_result = _ai_summarize(
        title=target["title"],
        description=target.get("description", ""),
        transcript=transcript,
        channel_name=channel_name,
    )

    if ai_result:
        base = ai_result
        logger.info(
            f"{channel_name}: AI summary done — bias: {ai_result.get('overall_bias','?')} "
            f"({ai_result.get('via','ai')})"
        )
    else:
        base = _description_summary(target["title"], target.get("description",""))
        logger.info(
            f"{channel_name}: AI failed — using description fallback "
            f"(bias: {base['overall_bias']})"
        )

    # Regex ticker extraction from transcript
    symbols = _extract_tickers(transcript or target.get("description",""), portfolio_symbols)
    # Merge with AI-found tickers
    ai_tickers = ai_result.get("tickers_discussed", []) if ai_result else []
    ai_sentiment = ai_result.get("ticker_sentiment", {}) if ai_result else {}
    seen_syms = {s["symbol"] for s in symbols}
    for t in ai_tickers:
        t = t.upper()
        if t not in seen_syms:
            symbols.append({
                "symbol":           t,
                "sentiment":        ai_sentiment.get(t, "neutral"),
                "action_mentioned": "none",
            })
            seen_syms.add(t)
        else:
            # Update sentiment with AI's assessment (more accurate than regex)
            for s in symbols:
                if s["symbol"] == t and t in ai_sentiment:
                    s["sentiment"] = ai_sentiment[t]

    price_levels = _extract_price_levels(transcript or "")

    analysis = {
        **base,
        "channel":         channel_name,
        "handle":          channel["handle"],
        "video_id":        video_id,
        "title":           target["title"],
        "published":       target["published"],
        "url":             target["url"],
        "symbols_mentioned": symbols,
        "price_levels":    price_levels,
    }

    # Cache (without cross_reference — recomputed fresh each run)
    cache[cache_key] = {k: v for k, v in analysis.items()
                        if k not in ("cross_reference",)}
    _save_cache(cache)

    analysis["cross_reference"] = cross_reference(symbols, signal_log)
    logger.info(
        f"{channel_name}: {len(symbols)} tickers, {len(price_levels)} price levels"
    )
    return analysis


def fetch_all_channels(
    portfolio_symbols: list[str],
    signal_log: dict,
    force: bool = False,
) -> list[dict]:
    results = []
    for channel in CHANNELS:
        try:
            result = fetch_channel_analysis(channel, portfolio_symbols, signal_log, force)
            if result:
                results.append(result)
        except Exception as e:
            logger.warning(f"{channel['name']}: failed — {e}")
    return results