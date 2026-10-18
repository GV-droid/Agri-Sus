"""Gemini client helpers: Vertex AI (default on Cloud Run) or Gemini API key; model fallback chain;
Google Search grounding with citations; Gemini-generated outlook for every plan year that feeds the simulator."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from functools import lru_cache

from .config import settings
from .data import MAX_HORIZON, PLAN_START


@lru_cache(maxsize=1)
def client():
    from google import genai
    if settings.use_vertex and settings.project:
        return genai.Client(vertexai=True, project=settings.project, location=settings.location)
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key)
    raise RuntimeError("Gemini not configured: set GOOGLE_CLOUD_PROJECT (Vertex AI) or GEMINI_API_KEY")


_working_model: dict = {}
_no_thinking: set = set()  # models that rejected an explicit thinking budget; they keep their own default


def preferred_model(model: str) -> str:
    return _working_model.get(model, model)


def _tuned(config, model: str, timeout_s: float | None):
    """Per-call options: a capped thinking budget (latency) and a client-side timeout (a call can never hang)."""
    from google.genai import types
    upd = {}
    if settings.thinking_budget is not None and model not in _no_thinking and config.thinking_config is None:
        upd["thinking_config"] = types.ThinkingConfig(thinking_budget=settings.thinking_budget)
    t = settings.gemini_timeout_s if timeout_s is None else timeout_s
    if t and t > 0:
        upd["http_options"] = types.HttpOptions(timeout=int(t * 1000))
    return config.model_copy(update=upd) if upd else config


def _bad_request(e: Exception) -> bool:
    return getattr(e, "code", None) == 400 or "INVALID_ARGUMENT" in str(e)


def is_timeout(e: Exception) -> bool:
    return "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() or "DEADLINE_EXCEEDED" in str(e)


def generate(model: str, contents, config, timeout_s: float | None = None):
    """generate_content with a capped thinking budget and a timeout, falling back to older model ids if the
    preferred one is unavailable."""
    from google.genai import types
    config = config or types.GenerateContentConfig()
    chain = [preferred_model(model)] + [m for m in settings.fallback_models if m]
    last = None
    for m in dict.fromkeys(chain):
        cfg = _tuned(config, m, timeout_s)
        try:
            try:
                r = client().models.generate_content(model=m, contents=contents, config=cfg)
            except Exception as e:
                if cfg.thinking_config is None or config.thinking_config is not None or not _bad_request(e):
                    raise
                # this model does not accept the thinking budget: retry once with its default thinking
                r = client().models.generate_content(model=m, contents=contents,
                                                     config=cfg.model_copy(update={"thinking_config": None}))
                _no_thinking.add(m)
            _working_model[model] = m
            return r, m
        except Exception as e:  # model not found / not enabled in region -> try next
            last = e
            msg = str(e)
            if not any(x in msg for x in ("404", "NOT_FOUND", "not found", "403", "PERMISSION", "INVALID_ARGUMENT")):
                raise
    raise last


def _citations(resp) -> list:
    out = []
    try:
        gm = resp.candidates[0].grounding_metadata
        for ch in (gm.grounding_chunks or []):
            if ch.web:
                out.append({"title": ch.web.title, "uri": ch.web.uri})
        queries = list(gm.web_search_queries or [])
    except Exception:
        queries = []
    seen, uniq = set(), []
    for c in out:
        if c["uri"] not in seen:
            seen.add(c["uri"])
            uniq.append(c)
    return uniq, queries


def grounded_search(question: str) -> dict:
    """Gemini + Google Search grounding; returns answer text with web citations."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction="You are a research assistant for Indian agricultural policy. Answer concisely with "
                           "numbers, dates and the issuing agency. Prefer official sources (PIB, MoA&FW, IMD, CGWB, "
                           f"DGCIS, FAO, USDA). Today is {date.today():%d %B %Y}: use the most recent information "
                           "available and say how current each figure is.",
        temperature=0.2)
    resp, model = generate(settings.agent_model, question, cfg)
    cites, queries = _citations(resp)
    return {"answer": resp.text, "citations": cites[:8], "search_queries": queries, "model": model}


OUTLOOK_YEARS = list(range(PLAN_START, PLAN_START + MAX_HORIZON))  # one cached outlook serves every plan horizon

FORECAST_PROMPT = f"""You are the forecasting agent of Agri Sus, India's agricultural policy simulator.
Use Google Search to gather the latest official and expert outlooks, preferring those issued in the last three
months (IMD/WMO/NOAA ENSO forecasts, IMD long-range monsoon outlooks, USDA/FAO/World Bank commodity price outlooks for
palm oil, soybean oil and pulses, Indian import duty changes). Then produce a quantitative outlook for India for
agri-years {OUTLOOK_YEARS[0]}-{OUTLOOK_YEARS[-1]}.

Return ONLY a JSON object (no markdown) with this schema:
{{"generated_for": "{OUTLOOK_YEARS[0]}-{OUTLOOK_YEARS[-1]}",
 "years": [{{"year": {OUTLOOK_YEARS[0]}, "enso_phase": "El Nino|La Nina|Neutral", "kharif_rain_pct_vs_normal": number (-30..+20),
            "rabi_tmax_anomaly_c": number (-1..3), "confidence": "low|medium|high"}}, ... one entry for each of
            {", ".join(map(str, OUTLOOK_YEARS))}],
 "import_price_multipliers": {{"edible_oils": number (0.6..2.0, 1.0 = 2024-25 landed price), "pulses": number}},
 "rationale": "max 120 words, cite the agencies/reports you used"}}
Be calibrated: beyond ~9 months ENSO skill is low, so lean towards climatology (0) with low confidence; for the
later years use climatology unless there is a clear multi-year signal."""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in model output")
    return json.loads(m.group(0))


_forecast_cache: dict = {}
_forecast_lock = threading.Lock()


def outlook_cached() -> bool:
    return _forecast_cache.get("t", 0) > time.time() - 6 * 3600


def gemini_outlook(force: bool = False) -> dict:
    """Gemini (grounded) monsoon / heat / import-price outlook for every possible plan year, cached for 6 h. One call at a time: concurrent
    requests on a cold cache wait for the first instead of each paying for a grounded Gemini call."""
    with _forecast_lock:
        if not force and outlook_cached():
            return _forecast_cache["v"]
        from google.genai import types
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
        prompt = f"Today is {date.today():%d %B %Y}.\n" + FORECAST_PROMPT
        resp, model = generate(settings.forecast_model, prompt, cfg, timeout_s=max(150.0, settings.gemini_timeout_s))
        data = _extract_json(resp.text)
        cites, queries = _citations(resp)
        data.update({"citations": cites[:10], "search_queries": queries, "model": model,
                     "source": "gemini+google_search", "as_of": date.today().isoformat()})
        _forecast_cache.update(t=time.time(), v=data)
        return data


def outlook_to_params(o: dict) -> dict:
    shocks = []
    for y in o.get("years", []):
        try:
            if int(y["year"]) not in OUTLOOK_YEARS:
                continue
            shocks.append({"year": int(y["year"]),
                           "kharif_rain_pct": max(-30.0, min(20.0, float(y.get("kharif_rain_pct_vs_normal", 0)))),
                           "rabi_tmax_c": max(-1.0, min(3.0, float(y.get("rabi_tmax_anomaly_c", 0))))})
        except (KeyError, ValueError, TypeError):
            continue
    mult = {k: max(0.6, min(2.0, float(v))) for k, v in (o.get("import_price_multipliers") or {}).items()
            if k in ("edible_oils", "pulses")}
    return {"shocks": shocks, "import_price_mult": mult}
