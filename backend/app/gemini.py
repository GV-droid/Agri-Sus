"""Gemini client helpers: Vertex AI (default on Cloud Run) or Gemini API key; model fallback chain;
Google Search grounding with citations; Gemini-generated outlook for every plan year that feeds the simulator."""
from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from contextvars import ContextVar
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
_no_thinking: set = set()  # models that rejected an explicit thinking config; they keep their own default

# Set by the co-pilot around its tool calls: every Gemini call made inside (grounded search, outlook, document
# writer) then finishes by the answer's deadline and reports retries to the visitor's activity feed.
call_deadline: ContextVar = ContextVar("gemini_call_deadline", default=None)
call_notify: ContextVar = ContextVar("gemini_call_notify", default=None)


def _scope(deadline: float | None, on_retry):
    d = call_deadline.get()
    if d is not None:
        deadline = d if deadline is None else min(deadline, d)
    return deadline, on_retry or call_notify.get()


class Cancelled(Exception):
    """The visitor stopped the answer."""


class StreamTimeout(TimeoutError):
    """A streamed call produced no first chunk, or no new chunk, in time: treated as stalled and retried."""


def preferred_model(model: str) -> str:
    return _working_model.get(model, model)


def _tuned(config, model: str, timeout_s: float | None, fast: bool = False, thoughts: bool = False):
    """Per-call options: a capped thinking budget (smaller on a retry), thought summaries when the caller shows them,
    and a client-side timeout (a call can never hang)."""
    from google.genai import types
    upd = {}
    if model not in _no_thinking and config.thinking_config is None:
        budget = settings.fast_thinking_budget if fast else settings.thinking_budget
        if budget is not None or thoughts:
            upd["thinking_config"] = types.ThinkingConfig(thinking_budget=budget, include_thoughts=True if thoughts else None)
    t = settings.gemini_timeout_s if timeout_s is None else timeout_s
    if t and t > 0:
        upd["http_options"] = types.HttpOptions(timeout=int(max(t, 1) * 1000))
    return config.model_copy(update=upd) if upd else config


def _bad_request(e: Exception) -> bool:
    return getattr(e, "code", None) == 400 or "INVALID_ARGUMENT" in str(e)


def is_timeout(e: Exception) -> bool:
    return "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() or "DEADLINE_EXCEEDED" in str(e)


_NET_ERRORS = {"RemoteProtocolError", "ReadError", "ConnectError", "WriteError", "PoolTimeout", "ProtocolError",
               "IncompleteRead", "ConnectionResetError", "ChunkedEncodingError"}
_TRANSIENT_TEXT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "overloaded", "Overloaded",
                   "try again later", "Server disconnected", "INTERNAL")


def is_transient(e: Exception) -> bool:
    """Worth retrying: a stall or timeout, rate limiting, an overloaded or failing backend, a dropped connection."""
    if isinstance(e, StreamTimeout) or is_timeout(e):
        return True
    if getattr(e, "code", None) in (408, 429, 500, 502, 503, 504):
        return True
    return type(e).__name__ in _NET_ERRORS or any(x in str(e) for x in _TRANSIENT_TEXT)


def _unavailable(e: Exception) -> bool:
    msg = str(e)
    return any(x in msg for x in ("404", "NOT_FOUND", "not found", "403", "PERMISSION", "INVALID_ARGUMENT"))


def _why(e: Exception) -> str:
    code = getattr(e, "code", None)
    if isinstance(e, StreamTimeout) or is_timeout(e):
        return "the model was slow to respond"
    if code == 429 or "RESOURCE_EXHAUSTED" in str(e):
        return "the model is rate-limited right now"
    if code in (500, 502, 503, 504) or "overloaded" in str(e).lower():
        return "the model is overloaded right now"
    return "the connection to the model dropped"


class _Plan:
    """Which model to call next. An unavailable model id (404/403/400) moves down the fallback chain at once; a
    transient failure (stall, timeout, 429, 5xx, dropped connection) is retried up to settings.gemini_retries times,
    on the next model in the chain and with a small thinking budget, so a slow call is retried faster instead of
    failing the whole answer. A fallback is remembered as the preferred model only when the primary is unavailable."""

    def __init__(self, model: str):
        self.model = model
        self.chain = list(dict.fromkeys([preferred_model(model)] + [m for m in settings.fallback_models if m]))
        self.i, self.fast, self.retries, self.hops_only, self.last = 0, False, max(0, settings.gemini_retries), True, None

    def current(self) -> str | None:
        return self.chain[self.i] if self.i < len(self.chain) else None

    def succeeded(self) -> None:
        if self.i and self.hops_only:
            _working_model[self.model] = self.chain[self.i]

    def failed(self, e: Exception) -> str | None:
        """Record a failure -> "" (try the next model silently), a reason to show (retry), or None (give up)."""
        self.last = e
        if _unavailable(e) and not is_transient(e):
            self.i += 1
            return "" if self.i < len(self.chain) else None
        if not is_transient(e) or self.retries <= 0:
            return None
        self.retries -= 1
        self.hops_only, self.fast = False, True
        self.i = min(self.i + 1, len(self.chain) - 1)
        return _why(e)

    def give_up(self) -> Exception:
        return self.last or StreamTimeout("no time left for another model call")


def _backoff(e: Exception | None) -> None:
    if e is not None and (getattr(e, "code", None) in (429, 503) or "RESOURCE_EXHAUSTED" in str(e)):
        time.sleep(1.0 + random.random() * 1.5)
    else:
        time.sleep(0.2 + random.random() * 0.3)


def _call_once(m: str, contents, cfg, original):
    try:
        return client().models.generate_content(model=m, contents=contents, config=cfg)
    except Exception as e:
        if cfg.thinking_config is None or original.thinking_config is not None or not _bad_request(e):
            raise
        # this model does not accept the thinking config: retry once with its default thinking
        r = client().models.generate_content(model=m, contents=contents,
                                             config=cfg.model_copy(update={"thinking_config": None}))
        _no_thinking.add(m)
        return r


def generate(model: str, contents, config, timeout_s: float | None = None, deadline: float | None = None,
             on_retry=None):
    """generate_content with a capped thinking budget and a per-call timeout, retried on transient failures (on the
    next model, thinking less) and falling back to other model ids if the preferred one is unavailable.
    deadline: time.monotonic() by which the whole thing must be done (no new attempt starts after it).
    on_retry(reason): called before a retry, so callers can show it. Returns (response, model id)."""
    from google.genai import types
    config = config or types.GenerateContentConfig()
    base = settings.gemini_timeout_s if timeout_s is None else timeout_s
    deadline, on_retry = _scope(deadline, on_retry)
    plan = _Plan(model)
    while True:
        m = plan.current()
        left = None if deadline is None else deadline - time.monotonic()
        if m is None or (left is not None and left < 3):
            raise plan.give_up()
        cfg = _tuned(config, m, base if left is None else min(base, left), fast=plan.fast)
        try:
            r = _call_once(m, contents, cfg, config)
            plan.succeeded()
            return r, m
        except Exception as e:
            why = plan.failed(e)
            if why is None:
                raise
            if why and on_retry is not None:
                on_retry(why)
            print(f"[gemini] {m}: {type(e).__name__}: {str(e)[:160]} -> {'retry' if why else 'next model'}")
            if why:
                _backoff(e)


def _stream_once(m: str, contents, cfg, q: queue.Queue, stop: threading.Event) -> None:
    """Producer thread: push every chunk of one streamed call into q (then 'end', or 'err')."""
    it = None
    try:
        it = client().models.generate_content_stream(model=m, contents=contents, config=cfg)
        for chunk in it:
            q.put(("chunk", chunk))
            if stop.is_set():
                break
        q.put(("end", None))
    except Exception as e:
        q.put(("err", e))
    finally:
        close = getattr(it, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def _chunks(m: str, contents, cfg, first_s: float, idle_s: float, end_at: float, cancelled):
    """Chunks of one streamed call. A stall is detected by silence, not by total length: StreamTimeout when the
    first chunk takes more than first_s or a later one more than idle_s; a long answer that keeps streaming is fine
    until end_at. The producer runs in its own thread so a stalled network read cannot block the caller."""
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=_stream_once, args=(m, contents, cfg, q, stop), daemon=True, name="gemini-stream").start()
    last, got = time.monotonic(), False
    try:
        while True:
            try:
                kind, val = q.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                if cancelled is not None and cancelled():
                    raise Cancelled()
                limit = idle_s if got else first_s
                if now - last > limit:
                    raise StreamTimeout(f"no {'new output' if got else 'first output'} from {m} in {limit:.0f} s")
                if now > end_at:
                    raise StreamTimeout(f"{m} ran out of time")
                continue
            if kind == "err":
                raise val
            if kind == "end":
                return
            got, last = True, time.monotonic()
            yield val
    finally:
        stop.set()


def _add_part(parts: list, p) -> None:
    """Accumulate streamed parts: consecutive plain text of the same kind is merged; anything carrying a thought
    signature (Gemini 3 needs those back in the history) or a function call is kept as its own part."""
    from google.genai import types
    prev = parts[-1] if parts else None
    if (prev is not None and p.text is not None and prev.text is not None and not p.thought_signature
            and not prev.thought_signature and not prev.function_call and bool(prev.thought) == bool(p.thought)):
        parts[-1] = types.Part(text=prev.text + p.text, thought=True if p.thought else None)
    else:
        parts.append(p)


def stream(model: str, contents, config, *, deadline: float | None = None, first_s: float | None = None,
           idle_s: float | None = None, total_s: float = 300.0, cancelled=None, thoughts: bool = False):
    """Streamed generate_content with stall detection and retries. Yields events:
    ("model", id) when an attempt starts, ("thought", text) thought-summary deltas (thoughts=True),
    ("text", text) answer deltas, ("retry", reason) before a retry, ("reset", None) when a failed attempt had already
    streamed answer text (discard it), and finally ("final", (Content, model id, finish reason)).
    Raises the last error when every attempt failed, or Cancelled."""
    from google.genai import types
    config = config or types.GenerateContentConfig()
    first_s = first_s or settings.gemini_first_token_s
    idle_s = idle_s or settings.gemini_idle_s
    deadline = _scope(deadline, None)[0]
    plan = _Plan(model)
    while True:
        m = plan.current()
        left = None if deadline is None else deadline - time.monotonic()
        if m is None or (left is not None and left < 5):
            raise plan.give_up()
        call_s = total_s if left is None else min(total_s, left)
        cfg = _tuned(config, m, call_s, fast=plan.fast, thoughts=thoughts)
        yield ("model", m)
        parts: list = []
        finish, emitted = None, False
        try:
            for chunk in _chunks(m, contents, cfg, first_s, idle_s, time.monotonic() + call_s, cancelled):
                cand = (chunk.candidates or [None])[0]
                if cand is None:
                    continue
                if cand.finish_reason:
                    finish = cand.finish_reason
                for p in ((cand.content.parts or []) if cand.content else []):
                    if p.function_call:
                        parts.append(p)
                    elif p.text is not None or p.thought_signature:
                        if p.text:
                            if not p.thought:
                                emitted = True
                            yield ("thought" if p.thought else "text", p.text)
                        _add_part(parts, p)
                    else:
                        parts.append(p)
            plan.succeeded()
            # thought summaries are for display only; parts carrying a thought signature go back to the model
            parts = [p for p in parts if not (p.thought and not p.thought_signature)]
            yield ("final", (types.Content(role="model", parts=parts), m, str(getattr(finish, "value", finish) or "")))
            return
        except Cancelled:
            raise
        except Exception as e:
            if (not parts and cfg.thinking_config is not None and config.thinking_config is None
                    and _bad_request(e) and not is_transient(e) and m not in _no_thinking):
                _no_thinking.add(m)  # this model rejects the thinking config: same model again, its default thinking
                continue
            why = plan.failed(e)
            print(f"[gemini] stream {m}: {type(e).__name__}: {str(e)[:160]} -> "
                  f"{'give up' if why is None else 'retry' if why else 'next model'}")
            if why is None:
                raise
            if emitted:
                yield ("reset", None)
            if why:
                yield ("retry", why)
                _backoff(e)


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


def grounded_search(question: str, deadline: float | None = None) -> dict:
    """Gemini + Google Search grounding; returns answer text with web citations."""
    from google.genai import types
    cfg = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction="You are a research assistant for Indian agricultural policy. Answer concisely with "
                           "numbers, dates and the issuing agency. Prefer official sources (PIB, MoA&FW, IMD, CGWB, "
                           f"DGCIS, FAO, USDA). Today is {date.today():%d %B %Y}: use the most recent information "
                           "available and say how current each figure is.",
        temperature=0.2)
    resp, model = generate(settings.agent_model, question, cfg,
                           deadline=deadline or time.monotonic() + 2 * settings.gemini_timeout_s)
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
        resp, model = generate(settings.forecast_model, prompt, cfg, timeout_s=max(150.0, settings.gemini_timeout_s),
                               deadline=time.monotonic() + 300)
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
