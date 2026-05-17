"""
youtube_fetcher.py — Multi-channel YouTube analysis for morning report
───────────────────────────────────────────────────────────────────────
Monitors YouTube channels for new videos and extracts market signals.

Pipeline:
  1. Fetch latest video metadata via YouTube RSS (no API key)
  2. Download transcript via youtube-transcript-api (no API key)
  3. Regex-scan full transcript for price levels and ticker mentions
  4. Build summary from video description (always works, no AI)
  5. If ANTHROPIC_API_KEY set: upgrade to Claude for richer key points
  6. Cross-reference mentioned symbols against your EOD signals

Channels configured:
  Figuring Out Money  (@FiguringOutMoney)  — Mon/Wed/Fri, max age 3d
  The Stocks Channel  (@thestockschannel)  — irregular,   max age 7d

Install: pip install youtube-transcript-api
Place in: swing_signal_engine/signals/youtube_fetcher.py
"""

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# ── Channel registry ──────────────────────────────────────────────────────────
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
OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODEL  = "llama3.2:3b"
FETCH_TIMEOUT = 10

# Optional: set ANTHROPIC_API_KEY in .env for Claude-powered analysis (~$0.002/video)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-haiku-4-5-20251001"

# Tickers to always watch for in transcripts
COMMON_TICKERS = {
    "QQQ","SPY","TQQQ","SQQQ","NVDA","SMH","AAPL","MSFT","AMZN","GOOGL",
    "META","TSLA","AMD","INTC","TSM","AVGO","MU","LRCX","AMAT","PLTR",
    "GLD","GLDM","GDE","PSLV","IBIT","DBC","DBMF","VUG","AVUV",
    "RKLB","ARIS","GEV","JPM","PAAS","AG","SNDK","REMX","EWT","EWY",
    "EWJV","GRID","NANR","SPMO","UFO","URA","DRAM","QQQM",
    "XLY","XLK","XLF","XLE","XLC","TLT","IWM","DIA",
}

BULL_WORDS = [
    "support","buy","bullish","long","breakout","higher","upside","bounce",
    "rally","strength","accumulate","holding","above","ripping","surge",
]
BEAR_WORDS = [
    "resistance","sell","bearish","short","breakdown","lower","downside",
    "weak","distribution","caution","warning","breaking","below","failed",
    "collapse","diverge","divergence","declining","danger","beneath",
]


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


# ── RSS feed ──────────────────────────────────────────────────────────────────

def get_latest_videos(channel_id: str, max_results: int = 5) -> list[dict]:
    """Fetch latest video metadata + description via YouTube RSS. No API key."""
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
        entries = root.findall("atom:entry", ns)
        videos  = []

        for entry in entries[:max_results]:
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
    """Fetch full transcript, cleaned of filler. No API key required."""
    FILLER_STARTS = {
        "subscribe", "like and subscribe", "make sure to subscribe",
        "welcome back", "what is up", "hey guys", "hey everyone",
        "smash the like",
    }
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        fetched = YouTubeTranscriptApi().fetch(video_id)
        text    = " ".join(s.text for s in fetched)
        text    = re.sub(r"\[.*?\]", "", text)
        text    = re.sub(r"\s+", " ", text).strip()

        # Strip obvious opening filler
        sentences = re.split(r"(?<=[.!?])\s+", text)
        clean = []
        for i, sent in enumerate(sentences):
            lower = sent.lower().strip()
            if i < 3 and any(lower.startswith(f) for f in FILLER_STARTS):
                continue
            clean.append(sent)
        return " ".join(clean)

    except ImportError:
        logger.error("pip install youtube-transcript-api")
        return None
    except Exception as e:
        logger.debug(f"Transcript unavailable for {video_id}: {e}")
        return None


# ── Bias detection ────────────────────────────────────────────────────────────

def _bias_from_text(text: str) -> str:
    """Score text for bull/bear bias using keyword counts."""
    t    = text.lower()
    bull = sum(1 for w in BULL_WORDS if w in t)
    bear = sum(1 for w in BEAR_WORDS if w in t)
    if   bull > bear * 1.5: return "bullish"
    elif bear > bull * 1.5: return "bearish"
    elif bear > bull:       return "mixed"
    else:                   return "neutral"


# ── Price level extraction (regex, no AI) ────────────────────────────────────

def _extract_price_levels(text: str, portfolio_symbols: list[str]) -> list[dict]:
    """
    Regex-scan full transcript for price levels per ticker.
    Finds: support, resistance, expected ranges, targets.
    Instant — no AI required.
    """
    ALL_TICKERS = COMMON_TICKERS | set(portfolio_symbols)

    CONTEXT_RE = {
        "support":             r"support\s+(?:at\s+|near\s+|around\s+|level\s+of\s+)?(\d{2,5}(?:\.\d{1,2})?)",
        "resistance":          r"resistance\s+(?:at\s+|near\s+|wall\s+at\s+|level\s+)?(\d{2,5}(?:\.\d{1,2})?)",
        "expected_range_low":  r"(?:range|between|from)\s+(\d{2,5}(?:\.\d{1,2})?)\s+(?:to|and|-)",
        "expected_range_high": r"(?:to|and|-)\s+(\d{2,5}(?:\.\d{1,2})?)\b",
        "target":              r"target\s+(?:of\s+|at\s+|is\s+)?(\d{2,5}(?:\.\d{1,2})?)",
    }

    results = {}
    for ticker in ALL_TICKERS:
        pattern = r"\b" + re.escape(ticker) + r"\b"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            window = text[m.start():min(len(text), m.end() + 200)]
            entry  = {"symbol": ticker.upper()}
            for key, pat in CONTEXT_RE.items():
                match = re.search(pat, window, re.IGNORECASE)
                if match:
                    try:
                        val = float(match.group(1))
                        if 1.0 <= val <= 100000.0:
                            entry[key] = val
                    except Exception:
                        pass
            if len(entry) > 1:
                existing = results.get(ticker.upper(), {"symbol": ticker.upper()})
                results[ticker.upper()] = {**existing,
                    **{k: v for k, v in entry.items() if k not in existing}}

    output = []
    for sym, data in results.items():
        if len(data) > 1:
            data["timeframe"] = "near term"
            data["notes"]     = "Extracted from transcript"
            output.append(data)
    return output


# ── Symbol extraction (regex, no AI) ─────────────────────────────────────────

def _extract_symbols(text: str, portfolio_symbols: list[str]) -> list[dict]:
    """Find all ticker mentions and infer sentiment from surrounding words."""
    ALL_TICKERS = COMMON_TICKERS | set(portfolio_symbols)
    results = []
    seen    = set()

    for ticker in ALL_TICKERS:
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
            "comment":          f"Mentioned — {sent} tone",
        })

    return results


# ── Key sentence extraction ───────────────────────────────────────────────────

def _extract_key_sentences(transcript: str, max_words: int = 250) -> str:
    """
    Score every sentence by information density and return the top ones.
    Used to build a condensed digest for the Anthropic API call.
    """
    KEY_TERMS = {
        "support","resistance","target","breakout","breakdown","yield",
        "inflation","recession","earnings","sector","weekly","monthly",
        "expected","range","consumer","discretionary","technology","energy",
        "rally","selloff","divergence","dispersion","correlation","breadth",
        "momentum","level","critical","key","watch","important","warning",
    }
    FILLER_PHRASES = {
        "subscribe","like and","like this video","welcome back","make sure to",
        "hit the bell","notification","comment below","patreon","discord",
        "in today's video","let's get into","we do reports","monday wednesday",
    }

    sentences = re.split(r"(?<=[.!?])\s+", transcript)
    scored = []
    for i, sent in enumerate(sentences):
        if len(sent.split()) < 6:
            continue
        s_lower = sent.lower()
        if any(f in s_lower for f in FILLER_PHRASES):
            continue
        score = 0
        score += len(re.findall(r"\$?\d{2,5}(?:\.\d{1,2})?(?:%|\s*percent)?", sent)) * 3
        score += sum(2 for t in COMMON_TICKERS
                     if re.search(r"\b" + re.escape(t) + r"\b", sent, re.IGNORECASE))
        score += sum(1 for t in KEY_TERMS if t in s_lower)
        score += min(3, len(sent.split()) // 10)
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
        if len(selected) >= 20:
            break

    selected.sort(key=lambda x: x[0])
    return " ".join(s for _, s in selected)


# ── Summary builder ───────────────────────────────────────────────────────────

def _description_summary(title: str, description: str) -> dict:
    """
    Build summary from video description — no AI required.
    The description is written by the creator and already summarizes the video.
    Bias comes from keyword analysis of the title.
    """
    # Title bias — double-weighted since it's the clearest signal
    bias = _bias_from_text(title + " " + title)
    if bias == "neutral" and description:
        bias = _bias_from_text(description[:300])

    # Clean description: skip short lines, links, and promotional text
    SKIP_PHRASES = {
        "subscribe", "discord", "patreon", "tradingview", "http",
        "►", "▶", "👉", "📌", "💬", "🔔",
    }
    paras = []
    for p in description.split("\n"):
        p = p.strip()
        if len(p) < 40:
            continue
        if any(s in p.lower() for s in SKIP_PHRASES):
            continue
        paras.append(p)

    # Summary = first paragraph, cut cleanly at a sentence boundary
    raw_summary = paras[0] if paras else title
    if len(raw_summary) > 600:
        cut = raw_summary[:600].rfind(".")
        raw_summary = raw_summary[:cut + 1] if cut > 100 else raw_summary[:600]

    # Key points = remaining paragraphs, deduped against summary
    summary_words = set(raw_summary.lower().split())
    key_points = []
    for para in paras[1:6]:
        # Strip list markers
        clean = re.sub(r"^[-•▸►*]\s+", "", para).strip()
        # Take first sentence if paragraph is long
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean)
                     if len(s.strip()) > 15]
        point = sentences[0][:150] if sentences else clean[:150]
        if not point:
            continue
        # Skip if too similar to summary (>60% word overlap)
        pt_words = set(point.lower().split())
        overlap  = len(pt_words & summary_words) / max(len(pt_words), 1)
        if overlap < 0.6:
            key_points.append(point)
        if len(key_points) >= 3:
            break

    # If no distinct key points found, extract unique sentences from summary
    if not key_points:
        all_sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw_summary)
                     if len(s.strip()) > 30]
        # Use sentences 2 onwards (first is already shown as summary headline)
        key_points = all_sents[1:4]

    return {
        "summary":      raw_summary,
        "overall_bias": bias,
        "week_outlook": "",
        "key_points":   key_points[:3] if key_points else [title],
    }


# ── Anthropic API (optional) ──────────────────────────────────────────────────

def _anthropic_analyze(prompt: str) -> str | None:
    """Call Anthropic Claude for richer analysis. ~3-5s, ~$0.002/video."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if resp.status_code == 200:
            content = resp.json().get("content", [])
            return content[0].get("text", "").strip() if content else None
        logger.debug(f"Anthropic error: {resp.status_code}")
        return None
    except Exception as e:
        logger.debug(f"Anthropic call failed: {e}")
        return None


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyze_video(
    title: str,
    description: str,
    full_transcript: str,
    channel_name: str,
    portfolio_symbols: list[str],
) -> dict:
    """
    Analyze a video using description + regex extraction.
    No AI required for basic analysis.
    Set ANTHROPIC_API_KEY in .env for Claude-powered key points (~$0.002/video).
    """
    # Step 1: instant regex extraction from full transcript
    price_targets = _extract_price_levels(full_transcript or "", portfolio_symbols)
    em_levels     = _extract_expected_moves(full_transcript or "")
    # Merge expected move levels into price_targets (avoid duplicates)
    existing_syms = {p.get("symbol") for p in price_targets}
    for em in em_levels:
        if em.get("symbol") not in existing_syms:
            price_targets.append(em)
        else:
            # Update existing entry with EM data
            for pt in price_targets:
                if pt.get("symbol") == em.get("symbol"):
                    pt.update({k: v for k, v in em.items() if k not in pt})
    symbols       = _extract_symbols(full_transcript or description, portfolio_symbols)
    logger.info(f"  Regex: {len(price_targets)} price level(s), {len(symbols)} symbol(s)")

    # Step 2: summary from description
    if ANTHROPIC_API_KEY:
        logger.info("  Summary via Claude API...")
        digest = _extract_key_sentences(full_transcript) if full_transcript else ""
        prompt = f"""Analyze this stock market video. JSON only, no markdown.

Channel: {channel_name}
Title: "{title}"
Description: {description[:500]}
Key transcript content: {digest[:600]}

{{"summary": "<2-3 sentences covering the main thesis and key insight>",
  "overall_bias": "<bullish|bearish|neutral|mixed>",
  "week_outlook": "<1 sentence about the week ahead, or empty string>",
  "key_points": ["<specific insight 1>", "<specific insight 2>", "<specific insight 3>"]}}

Rules:
- summary and key_points must be specific to this video, not generic
- overall_bias must match the title (breaking/warning = bearish)
- mention specific sectors, tickers, or levels if present in the content"""

        raw = _anthropic_analyze(prompt)
        base = None
        if raw:
            try:
                cleaned = raw
                if "```" in cleaned:
                    for part in cleaned.split("```"):
                        if "{" in part:
                            cleaned = part.lstrip("json").strip()
                            break
                s = cleaned.find("{")
                e = cleaned.rfind("}") + 1
                if s >= 0 and e > s:
                    base = json.loads(cleaned[s:e])
            except Exception:
                pass
        if not base:
            base = _description_summary(title, description)
    else:
        base = _description_summary(title, description)
        logger.info(f"  Description summary — bias: {base['overall_bias']}")

    # Step 3: bias sanity check — title overrides AI/description if clearly different
    final_bias       = base.get("overall_bias", "neutral")
    title_bias_check = _bias_from_text(title + " " + title)
    if title_bias_check in ("bearish", "bullish") and final_bias != title_bias_check:
        t_lower    = title.lower()
        bear_score = sum(1 for w in BEAR_WORDS if w in t_lower)
        bull_score = sum(1 for w in BULL_WORDS if w in t_lower)
        if abs(bear_score - bull_score) >= 1:
            base["overall_bias"] = title_bias_check
            final_bias = title_bias_check

    logger.info(f"  Done — bias: {final_bias}, "
                f"{len(price_targets)} price target(s), {len(symbols)} symbol(s)")
    return {**base, "price_targets": price_targets, "symbols_mentioned": symbols}



def _extract_expected_moves(text: str) -> list[dict]:
    """
    Extract options expected move levels from transcript.
    Catches phrases like:
      "752 to the upside, 725 to the downside"
      "upper expected move at 745"
      "lower expected move at 733"
      "expected move from 725 to 752"
    """
    results = []

    # Pattern: "NUMBER to the upside" / "NUMBER to the downside"
    upside_matches   = re.findall(
        r'(\d{3,5}(?:\.\d{1,2})?)\s+(?:and\s+some\s+change\s+)?to\s+the\s+upside',
        text, re.IGNORECASE)
    downside_matches = re.findall(
        r'(\d{3,5}(?:\.\d{1,2})?)\s+(?:and\s+some\s+change\s+)?to\s+the\s+downside',
        text, re.IGNORECASE)

    # Pattern: "upper ... move ... NUMBER"
    upper_matches = re.findall(
        r'upper\s+(?:\w+\s+){0,3}(?:move|bound|target)\s+(?:\w+\s+){0,4}?(?:at\s+|near\s+|around\s+)?(?:about\s+)?(\d{3,5}(?:\.\d{1,2})?)',
        text, re.IGNORECASE)
    lower_matches = re.findall(
        r'lower\s+(?:\w+\s+){0,3}(?:move|bound|target)\s+(?:\w+\s+){0,4}?(?:at\s+|near\s+|around\s+)?(?:about\s+)?(\d{3,5}(?:\.\d{1,2})?)',
        text, re.IGNORECASE)

    # "expected move from X to Y" or "pricing in X to Y"
    range_matches = re.findall(
        r'(?:expected\s+move|pricing\s+in|range\s+of)\s+(?:about\s+)?(\d{3,5}(?:\.\d{1,2})?)\s+to\s+(?:the\s+upside\s+)?(?:and\s+)?(\d{3,5}(?:\.\d{1,2})?)',
        text, re.IGNORECASE)

    # "move that's pricing in about 752 to the upside, 725 to the downside"
    # This is the most common format for The Stocks Channel
    inline = re.findall(
        r'pricing\s+in\s+(?:about\s+)?(\d{3,5}(?:\.\d{1,2})?)\s+to\s+the\s+upside[,\s]+(\d{3,5}(?:\.\d{1,2})?)\s+to\s+the\s+downside',
        text, re.IGNORECASE)

    all_upper = [float(v) for v in upside_matches + upper_matches if v]
    all_lower = [float(v) for v in downside_matches + lower_matches if v]

    for up, dn in inline:
        all_upper.append(float(up))
        all_lower.append(float(dn))

    for lo, hi in range_matches:
        lo_v, hi_v = float(lo), float(hi)
        if lo_v < hi_v:
            all_lower.append(lo_v)
            all_upper.append(hi_v)
        else:
            all_upper.append(lo_v)
            all_lower.append(hi_v)

    # Sanity filter: values must be in reasonable SPY/QQQ range (300-10000)
    all_upper = [v for v in all_upper if 300 <= v <= 10000]
    all_lower = [v for v in all_lower if 300 <= v <= 10000]

    if all_upper or all_lower:
        entry = {"symbol": "SPY", "timeframe": "this week", "notes": "Options expected move"}
        if all_upper: entry["expected_range_high"] = max(set(all_upper), key=all_upper.count)
        if all_lower: entry["expected_range_low"]  = min(set(all_lower), key=all_lower.count)
        results.append(entry)

    return results

# ── Cross-reference against EOD signals ──────────────────────────────────────

def cross_reference(yt_symbols: list[dict], signal_log: dict) -> list[dict]:
    """Compare YouTube-mentioned symbols against your EOD signals."""
    results = []
    for yt in yt_symbols:
        sym    = yt.get("symbol", "").upper()
        yt_sen = yt.get("sentiment", "neutral")

        eod_signal = eod_conv = eod_acct = None
        for key, sig in signal_log.items():
            if key.endswith(f":{sym}"):
                eod_signal = sig.get("signal", "HOLD")
                eod_conv   = sig.get("conviction", 0)
                eod_acct   = key.split(":")[0]
                break

        yt_bull = yt_sen == "bullish"
        yt_bear = yt_sen in ("bearish", "cautious")
        if eod_signal is None:
            alignment = "not_in_portfolio"
        elif (yt_bull and eod_signal in ("BUY","STRONG_BUY")) or \
             (yt_bear and eod_signal in ("SELL","STRONG_SELL")):
            alignment = "aligned"
        elif (yt_bull and eod_signal in ("SELL","STRONG_SELL")) or \
             (yt_bear and eod_signal in ("BUY","STRONG_BUY")):
            alignment = "conflict"
        else:
            alignment = "neutral"

        results.append({
            "symbol":           sym,
            "yt_sentiment":     yt_sen,
            "action_mentioned": yt.get("action_mentioned", ""),
            "yt_comment":       yt.get("comment", ""),
            "eod_signal":       eod_signal,
            "eod_conv":         eod_conv,
            "eod_acct":         eod_acct,
            "alignment":        alignment,
        })

    order = {"conflict": 0, "aligned": 1, "neutral": 2, "not_in_portfolio": 3}
    results.sort(key=lambda x: order.get(x["alignment"], 9))
    return results


# ── Per-channel fetch ─────────────────────────────────────────────────────────

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
    if not videos:
        return None

    target = next((v for v in videos if v["age_days"] <= max_age), None)
    if not target:
        logger.info(f"{channel_name}: no video within {max_age}d "
                    f"(latest: {videos[0]['age_days']}d ago)")
        return None

    video_id  = target["video_id"]
    cache_key = f"{channel_id}:{video_id}"

    # Skip bad cache entries (previous failed analysis)
    if not force and cache_key in cache:
        cached = cache[cache_key]
        is_bad = (
            cached.get("overall_bias") in ("unknown", None)
            and not cached.get("key_points")
        )
        if not is_bad:
            logger.info(f"{channel_name}: using cached analysis")
            result = dict(cached)
            result["cross_reference"] = cross_reference(
                result.get("symbols_mentioned", []), signal_log)
            return result
        logger.info(f"{channel_name}: retrying failed cache entry")

    logger.info(f"{channel_name}: '{target['title']}' "
                f"({target['published']}, {target['age_days']}d ago)")

    # Fetch full transcript
    logger.info(f"{channel_name}: fetching transcript...")
    full_transcript = get_full_transcript(video_id) or ""
    if full_transcript:
        logger.info(f"{channel_name}: {len(full_transcript.split())} words — analyzing...")
    else:
        logger.info(f"{channel_name}: no transcript — using description only")

    # Analyze
    analysis = analyze_video(
        title             = target["title"],
        description       = target.get("description", ""),
        full_transcript   = full_transcript,
        channel_name      = channel_name,
        portfolio_symbols = portfolio_symbols,
    )
    analysis.update({
        "channel":    channel_name,
        "handle":     channel["handle"],
        "video_id":   video_id,
        "title":      target["title"],
        "published":  target["published"],
        "url":        target["url"],
        "transcript": full_transcript,   # included for email appendix
    })

    # Cache without transcript (too large) and without cross_reference (changes daily)
    cache[cache_key] = {k: v for k, v in analysis.items()
                        if k not in ("cross_reference", "transcript")}
    _save_cache(cache)

    analysis["cross_reference"] = cross_reference(
        analysis.get("symbols_mentioned", []), signal_log)
    return analysis


# ── Entry point ───────────────────────────────────────────────────────────────

def fetch_all_channels(
    portfolio_symbols: list[str],
    signal_log: dict,
    force: bool = False,
) -> list[dict]:
    """Fetch and analyze latest video from all configured channels."""
    results = []
    for channel in CHANNELS:
        try:
            analysis = fetch_channel_analysis(
                channel, portfolio_symbols, signal_log, force=force)
            if analysis:
                results.append(analysis)
        except Exception as e:
            logger.warning(f"{channel['name']}: fetch failed — {e}")
    return results