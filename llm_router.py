"""
llm_router.py — Unified LLM provider with automatic fallback
──────────────────────────────────────────────────────────────
Priority chain:
  1. Gemini (Google AI Studio) — free, 1,500 req/day, frontier quality
     Set GEMINI_API_KEY in .env — get free key at aistudio.google.com
  2. Groq — free, 14,400 req/day, very fast (LPU inference)
     Set GROQ_API_KEY in .env — get free key at console.groq.com
  3. Ollama — local fallback, no API key needed, hardware-limited
     Must be running: ollama serve

Used by:
  Trading-bot:       strategies/ai_engine.py
  Swing signal:      signals/ai_engine.py
  Swing signal:      signals/youtube_fetcher.py
  Swing signal:      signals/gumshoe_fetcher.py
  Swing signal:      signals/news_fetcher.py

Usage:
  from llm_router import llm_call, llm_available

  result = llm_call(prompt, expect_json=True, timeout=25)
  if result is None:
      # all providers failed — use fallback logic

Environment variables (.env):
  GEMINI_API_KEY=AIza...     # primary — free at aistudio.google.com
  GROQ_API_KEY=gsk_...       # fallback — free at console.groq.com
  OLLAMA_MODEL=qwen3:4b      # local fallback model name
  LLM_PROVIDER=auto          # auto | gemini | groq | ollama (force a provider)
"""

import json
import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

# ── Provider config ────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY",   "")
OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen3:4b")
FORCE_PROVIDER  = os.getenv("LLM_PROVIDER", "auto").lower()   # auto|gemini|groq|ollama

# Gemini — use gemini-2.0-flash-lite for free tier (fastest, generous limits)
# gemini-2.0-flash also works but uses slightly more quota
GEMINI_MODEL    = "gemini-2.0-flash-lite"
GEMINI_URL      = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Groq — qwen3 32b is available free and smarter than most local models
GROQ_MODEL      = "qwen3-32b"
GROQ_FALLBACK   = "llama-3.3-70b-versatile"   # if qwen3-32b quota exhausted
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"

# Provider health cache — avoids retrying a provider that failed this session
_provider_state: dict[str, bool] = {
    "gemini": True,
    "groq":   True,
    "ollama": True,
}
_last_reset = time.time()
RESET_INTERVAL = 900   # re-try failed providers every 15 min

# Gemini free tier = 15 RPM. Track last call, enforce minimum gap.
_gemini_last_call: float = 0.0
GEMINI_MIN_INTERVAL: float = 4.2   # seconds between calls (15 RPM safe margin)


def _reset_if_stale():
    global _last_reset
    if time.time() - _last_reset > RESET_INTERVAL:
        for k in _provider_state:
            _provider_state[k] = True
        _last_reset = time.time()


def _clean_json(raw: str) -> str:
    """Strip markdown fences and <think> blocks from LLM response."""
    if not raw:
        return ""
    raw = raw.strip()
    # Remove <think>...</think> blocks (qwen3 chain-of-thought)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Strip ```json ... ``` fences
    if "```" in raw:
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
    # Extract first {...} block
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end+1]
    return raw.strip()


# ── Gemini ─────────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, timeout: int = 20) -> str | None:
    global _gemini_last_call
    if not GEMINI_API_KEY:
        return None
    # Enforce 15 RPM free tier limit
    elapsed = time.time() - _gemini_last_call
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)
    _gemini_last_call = time.time()
    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature":     0.1,
                    "maxOutputTokens": 1024,
                },
            },
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            candidates = resp.json().get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    _provider_state["gemini"] = True
                    return parts[0].get("text", "").strip()
        elif resp.status_code == 429:
            logger.warning("[LLM] Gemini rate limit hit — falling back to Groq")
            _provider_state["gemini"] = False
        elif resp.status_code in (401, 403):
            logger.warning(f"[LLM] Gemini auth error {resp.status_code} — check GEMINI_API_KEY")
            _provider_state["gemini"] = False
        else:
            logger.debug(f"[LLM] Gemini error {resp.status_code}: {resp.text[:100]}")
    except requests.exceptions.Timeout:
        logger.debug(f"[LLM] Gemini timed out after {timeout}s")
    except Exception as e:
        logger.debug(f"[LLM] Gemini call failed: {e}")
    return None


# ── Groq ───────────────────────────────────────────────────────────────────────

def _call_groq(prompt: str, timeout: int = 20, model: str = None) -> str | None:
    if not GROQ_API_KEY:
        return None
    use_model = model or GROQ_MODEL
    try:
        resp = requests.post(
            GROQ_URL,
            json={
                "model":       use_model,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens":  1024,
            },
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
        )
        if resp.status_code == 200:
            choices = resp.json().get("choices", [])
            if choices:
                _provider_state["groq"] = True
                return choices[0].get("message", {}).get("content", "").strip()
        elif resp.status_code == 429:
            # Rate limited — try fallback model
            if use_model != GROQ_FALLBACK:
                logger.debug(f"[LLM] Groq {use_model} rate limited, trying {GROQ_FALLBACK}")
                return _call_groq(prompt, timeout, model=GROQ_FALLBACK)
            logger.warning("[LLM] Groq rate limit hit on all models — falling back to Ollama")
            _provider_state["groq"] = False
        elif resp.status_code in (401, 403):
            logger.warning(f"[LLM] Groq auth error {resp.status_code} — check GROQ_API_KEY")
            _provider_state["groq"] = False
        else:
            logger.debug(f"[LLM] Groq error {resp.status_code}: {resp.text[:100]}")
    except requests.exceptions.Timeout:
        logger.debug(f"[LLM] Groq timed out after {timeout}s")
    except Exception as e:
        logger.debug(f"[LLM] Groq call failed: {e}")
    return None


# ── Ollama ─────────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str, timeout: int = 25) -> str | None:
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":   OLLAMA_MODEL,
                "prompt":  prompt,
                "stream":  False,
                "options": {"temperature": 0.1, "num_predict": 400},
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            _provider_state["ollama"] = True
            return resp.json().get("response", "").strip()
        logger.debug(f"[LLM] Ollama error {resp.status_code}")
    except requests.exceptions.ConnectionError:
        logger.debug("[LLM] Ollama not running (connection refused)")
        _provider_state["ollama"] = False
    except requests.exceptions.Timeout:
        logger.debug(f"[LLM] Ollama timed out after {timeout}s")
    except Exception as e:
        logger.debug(f"[LLM] Ollama call failed: {e}")
    return None


# ── Main router ────────────────────────────────────────────────────────────────

def llm_call(
    prompt:      str,
    expect_json: bool = True,
    timeout:     int  = 20,
    tag:         str  = "",        # optional label for logging (e.g. "grade_setup")
) -> dict | str | None:
    """
    Call the best available LLM provider in priority order:
      Gemini → Groq → Ollama

    Returns:
      dict   if expect_json=True and parsing succeeded
      str    if expect_json=False
      None   if all providers failed
    """
    _reset_if_stale()

    tag_str  = f"[{tag}] " if tag else ""
    order    = _provider_order()
    raw      = None
    provider = None

    for p in order:
        if not _provider_state.get(p, False):
            continue

        if p == "gemini":
            raw = _call_gemini(prompt, timeout=timeout)
        elif p == "groq":
            raw = _call_groq(prompt, timeout=timeout)
        elif p == "ollama":
            raw = _call_ollama(prompt, timeout=timeout)

        if raw:
            provider = p
            break

    if not raw:
        logger.debug(f"[LLM] {tag_str}all providers failed")
        return None

    logger.debug(f"[LLM] {tag_str}used {provider}")

    if not expect_json:
        return raw

    cleaned = _clean_json(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.debug(f"[LLM] {tag_str}JSON parse failed for response: {cleaned[:80]}")
        return None


def llm_call_text(prompt: str, timeout: int = 20, tag: str = "") -> str | None:
    """Convenience wrapper for non-JSON calls."""
    return llm_call(prompt, expect_json=False, timeout=timeout, tag=tag)


def llm_available() -> bool:
    """Return True if at least one provider is configured and likely available."""
    _reset_if_stale()
    if GEMINI_API_KEY and _provider_state["gemini"]:
        return True
    if GROQ_API_KEY and _provider_state["groq"]:
        return True
    # Try Ollama ping
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def llm_provider_status() -> dict:
    """Return current status of all providers — useful for startup logging."""
    _reset_if_stale()
    return {
        "gemini": {
            "configured": bool(GEMINI_API_KEY),
            "healthy":    _provider_state["gemini"],
            "model":      GEMINI_MODEL,
        },
        "groq": {
            "configured": bool(GROQ_API_KEY),
            "healthy":    _provider_state["groq"],
            "model":      GROQ_MODEL,
        },
        "ollama": {
            "configured": True,  # always available as fallback
            "healthy":    _provider_state["ollama"],
            "model":      OLLAMA_MODEL,
        },
        "active_order": _provider_order(),
    }


def _provider_order() -> list[str]:
    """Return provider priority order based on FORCE_PROVIDER env var."""
    if FORCE_PROVIDER == "gemini":
        return ["gemini"]
    if FORCE_PROVIDER == "groq":
        return ["groq", "ollama"]
    if FORCE_PROVIDER == "ollama":
        return ["ollama"]
    # auto: Gemini → Groq → Ollama
    return ["gemini", "groq", "ollama"]