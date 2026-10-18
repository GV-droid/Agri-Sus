"""FastAPI app: REST + SSE API and the compiled React UI (single Cloud Run service)."""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from . import agent, chats, documents, gemini, jobs, policies, service, trade
from . import store as persist
from .config import settings
from .data import PLAN_YEARS, store
from .optimizer import INSTRUMENTS
from .scenarios import BUDGET_MODES, HORIZON, MAX_TRADE_CUTS, SCENARIOS, sanitize_overrides, scenario_params

# policy levers exposed as UI controls; a scenario that presets one of them (e.g. gw_mode) tells the UI via /api/meta
LEVER_KEYS = {"max_shift_pct", "cereal_floor_pct", "gw_mode", "import_target_pct", "incentive_budget_cr",
              "rice_export_retention_pct", "price_realisation", "crop_growth_cap_pct", "budget_mode", "instruments",
              "trade_cuts"}
from .yield_model import METRICS_PATH

_SSE_PATHS = {"/api/agent/chat", "/api/documents"}


def _is_stream(path: str) -> bool:
    return path in _SSE_PATHS or (path.startswith("/api/jobs/") and path.endswith("/events"))


class _GZipExceptStreams(GZipMiddleware):
    """gzip responses, except Server-Sent Events: compression buffers them, so the co-pilot's live steps would only
    reach the browser all at once when the answer is complete."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _is_stream(scope.get("path") or ""):
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


# the UI's only third-party origin is the CARTO basemap (style, vector tiles, sprites and glyphs, all fetched);
# MapLibre's worker is served from /assets and decodes sprites into blob: images
_CSP = "; ".join([
    "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
    "font-src 'self' data:", "connect-src 'self' https://*.cartocdn.com", "worker-src 'self' blob:",
    "child-src 'self' blob:", "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'self'"])
_SECURITY_HEADERS = {
    "content-security-policy": _CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), geolocation=(), payment=(), usb=(), microphone=(self)",  # mic: voice questions
    "strict-transport-security": "max-age=31536000",
}
_DOCS_PATHS = ("/docs", "/redoc")  # Swagger UI / ReDoc load from a CDN (only served when API_DOCS=true)


class _SecurityHeaders:
    """Security headers on every response; hashed build assets are cached for a year (their names change on every
    build), index.html is revalidated so a redeploy shows up at once."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                h = MutableHeaders(scope=message)
                for k, v in _SECURITY_HEADERS.items():
                    if k == "content-security-policy" and settings.api_docs and path.startswith(_DOCS_PATHS):
                        continue
                    h.setdefault(k, v)
                if path.startswith("/assets/") and message["status"] == 200:
                    h["cache-control"] = "public, max-age=31536000, immutable"
            await send(message)

        await self.app(scope, receive, send_with_headers)


MAX_BODY_BYTES = 512 * 1024  # largest legitimate body: a long co-pilot conversation (the UI sends the last 12 turns)


class _BodyLimit:
    """Refuse oversized request bodies before any parsing (413), with or without a Content-Length header."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        cl = dict(scope["headers"]).get(b"content-length")
        if cl is not None and (not cl.isdigit() or int(cl) > self.max_bytes):
            return await self._too_large(scope, receive, send)
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.max_bytes:
                return await self._too_large(scope, receive, send)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()  # afterwards: wait for the disconnect (streaming responses listen for it)

        await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(scope, receive, send):
        await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)


_docs = settings.api_docs
app = FastAPI(title="Agri Sus API", version="1.0.0", docs_url="/docs" if _docs else None,
              redoc_url="/redoc" if _docs else None, openapi_url="/openapi.json" if _docs else None)
app.add_middleware(_GZipExceptStreams, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(_BodyLimit)
app.add_middleware(_SecurityHeaders)

_hits: dict = defaultdict(deque)


def _client_ip(req: Request) -> str:
    # Cloud Run's front end appends the real client address as the LAST X-Forwarded-For hop;
    # anything to its left is client-supplied and must not be trusted for rate limiting.
    xff = req.headers.get("x-forwarded-for")
    ip = xff.split(",")[-1].strip()[:64] if xff else (req.client.host if req.client else "?")
    if ":" in ip:  # IPv6: one subscriber gets a whole /64, so limit per /64 rather than per address
        try:
            return str(ipaddress.IPv6Network(f"{ip}/64", strict=False).network_address) + "/64"
        except ValueError:
            pass
    return ip


def _rate_limit(req: Request, limit: int = 30, window: int = 600, bucket: str = "agent", who: str | None = None) -> None:
    key = (bucket, who or _client_ip(req))
    now = time.time()
    if len(_hits) > 10000:  # evict idle buckets so the table cannot grow without bound
        for k in [k for k, q in _hits.items() if not q or q[-1] < now - window]:
            del _hits[k]
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests - please wait a few minutes." if who is None else
                            "The AI features are very busy right now - please try again in a few minutes.")
    q.append(now)


def _gemini_quota(req: Request) -> None:
    """Instance-wide ceiling on Gemini-backed requests, on top of the per-visitor limits."""
    if settings.gemini_enabled:
        _rate_limit(req, limit=settings.gemini_requests_per_10min, window=600, bucket="gemini", who="*all*")


def _solve_quota(req: Request) -> None:
    _rate_limit(req, limit=40, window=600, bucket="simulate")


@app.on_event("startup")
def _warm() -> None:
    def w():
        store()
        persist.status()  # connect the policy/chat/run stores once, off the request path
        for sc in SCENARIOS:  # baseline first, then every crisis scenario so demo clicks are instant: loaded from the
            try:              # shared run store when an earlier instance already solved them, else solved here
                t = time.time()
                service.run(sc)
                print(f"[warm] {sc}: ready in {time.time() - t:.1f}s")
            except Exception as e:  # pragma: no cover
                print(f"[warm] {sc}: {e}")
    threading.Thread(target=w, daemon=True).start()


@app.get("/api/health")
def health():
    return {"status": "ok", "gemini": settings.gemini_enabled, "data_backend": settings.data_backend,
            "project": bool(settings.project), "store": persist.status(), "jobs": jobs.stats()}


@app.get("/api/meta")
def meta():
    return _meta()


@lru_cache(maxsize=1)
def _meta() -> dict:
    with open(METRICS_PATH) as f:
        metrics = json.load(f)
    return {
        "plan_years": PLAN_YEARS,
        "horizon": HORIZON,
        "scenarios": [{"id": k, "label": v["label"], "description": v["description"],
                       "levers": {a: x for a, x in scenario_params(k).items() if a in LEVER_KEYS},
                       # year a climate shock hits, so the UI can show it (other years are unaffected by design)
                       "focus_year": min((x["year"] for x in v["params"].get("shocks", [])), default=None)}
                      for k, v in SCENARIOS.items()],
        "gemini": {"enabled": settings.gemini_enabled, "agent_model": settings.agent_model,
                   "forecast_model": settings.forecast_model,
                   "backend": "Vertex AI" if settings.use_vertex and settings.project else
                   ("Gemini API" if settings.gemini_api_key else "offline")},
        "data_backend": store().data_source,
        "yield_model": metrics,
        # champion/challenger state: agronomic prior vs BigQuery ML boosted tree (bigquery/02_bqml_yield_model.sql)
        "yield_engine": store().yield_engine,
        "climate_source": store().climate_source,
        "states": sorted(store().context.state_name.unique().tolist()),
        "n_districts": int(store()["district_crop_candidates"].district_code.nunique()),
        "yield_rows_repaired": int(getattr(store(), "yield_rows_repaired", 0)),
        "languages": agent.LANGUAGES,
        "budget_modes": [{"id": k, "label": v} for k, v in BUDGET_MODES.items()],
        "instruments": [{"id": k, "label": v} for k, v in INSTRUMENTS.items()],
        "store": persist.status(),
        "trade": {"fiscal_year": trade.latest_fy(), "max_cuts": MAX_TRADE_CUTS,
                  "products": [{"product": p_["product"], "group": p_["group"], "total_kt": p_["total_kt"],
                                "suppliers": [x["country"] for x in p_["suppliers"][:6]]}
                               for p_ in trade.suppliers(top=6)["products"]]},
    }


@app.get("/api/model-card")
def model_card():
    with open(METRICS_PATH) as f:
        return json.load(f)


# Districts carved out after the crop statistics were compiled -> parent district whose data covers their land.
# (Rajasthan reverted 9 of its 2023 districts to their parents in Dec 2024; Manipur 2016, Assam 2022.)
CARVED_TO_PARENT = {
    779: 104, 780: 92, 778: 107, 781: 86, 769: 102, 776: 100, 773: 114, 783: 102, 771: 113,  # Rajasthan
    714: 258, 715: 254, 713: 255, 716: 253, 711: 259, 712: 257, 717: 260,  # Manipur
    739: 280, 756: 616,  # Assam
}
NO_CROP_DATA = {"Delhi": "urban - no district crop statistics", "Lakshadweep": "islands - not covered by APY",
                "Kolkata": "urban - no district crop statistics", "Mahe": "enclave - not covered by APY",
                "Yanam": "enclave - not covered by APY"}


@lru_cache(maxsize=1)
def _geo_with_data_codes() -> bytes:
    gj = store().geojson
    have = set(store()["district_crop_candidates"].district_code.unique().tolist())
    names = store().context.set_index("district_code").district_name.to_dict()
    feats = []
    for f in gj["features"]:
        pr = dict(f["properties"])
        code = pr.get("district_code")
        if code in have:
            pr["data_code"] = code
        elif code in CARVED_TO_PARENT and CARVED_TO_PARENT[code] in have:
            pr["data_code"] = CARVED_TO_PARENT[code]
            pr["data_note"] = f"carved from {names.get(CARVED_TO_PARENT[code], 'parent district')} - parent data shown"
        else:
            pr["data_code"] = None
            pr["data_note"] = NO_CROP_DATA.get(pr.get("state_name"), NO_CROP_DATA.get(pr.get("district_name"),
                                               "no district crop statistics available"))
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": pr})
    return json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")).encode()


@app.get("/api/geo")
def geo():
    return Response(_geo_with_data_codes(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/districts")
def districts():
    ctx = store().context
    cols = ["district_code", "district_name", "state_name", "net_sown_area_ha", "irrigation_coverage_pct",
            "cgwb_stage_of_extraction_pct", "cgwb_category", "soil_n_kg_ha_est", "lat", "lon"]
    return json.loads(ctx[cols].to_json(orient="records"))


class SimRequest(BaseModel):
    scenario: str = Field("baseline", max_length=40)
    overrides: dict = {}


def _run_response(res: dict, request: Request) -> Response:
    """A plan as JSON, gzipped once per run and reused (the UI fetches the same ~2.5 MB plan repeatedly)."""
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(service.public_gzip(res), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return JSONResponse(service.public(res))


@app.post("/api/simulate")
def simulate(req: SimRequest, request: Request):
    try:
        req.overrides = sanitize_overrides(req.overrides)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.scenario not in SCENARIOS and req.scenario != "gemini_outlook":
        raise HTTPException(400, "unknown scenario")
    if req.scenario != "gemini_outlook":
        hit = service.peek(req.scenario, req.overrides)
        if hit is not None:  # cached and stored runs are free; only new solves count against the rate limit
            return _run_response(hit, request)
    _solve_quota(request)
    if req.scenario == "gemini_outlook":
        if not settings.gemini_enabled:
            raise HTTPException(503, "Gemini not configured")
        if not gemini.outlook_cached():
            _gemini_quota(request)
        try:
            o = gemini.gemini_outlook()
        except Exception as e:
            raise HTTPException(503, f"The Gemini outlook is unavailable right now ({type(e).__name__}); please retry.")
        ov = gemini.outlook_to_params(o)
        ov.update({k: v for k, v in req.overrides.items() if k not in ("shocks", "import_price_mult")})
        res = service.run("baseline", ov)
        out = service.public(res)
        out["params"] = {**out["params"], "scenario": "gemini_outlook"}  # copy: never mutate the cached run
        out["outlook"] = o
        return out
    return _run_response(service.run(req.scenario, req.overrides), request)


@app.get("/api/solve-estimate")
def solve_estimate():
    return {"seconds": service.solve_estimate_s()}


@app.get("/api/run/{run_id}")
def get_run(run_id: str, request: Request):
    if not service.valid_run_id(run_id):
        raise HTTPException(400, "invalid run id")
    r = service.get(run_id)
    if not r:
        raise HTTPException(404, "run expired")
    return _run_response(r, request)


@app.get("/api/district/{code}")
def district(code: int, run_id: Optional[str] = None):
    r = service.get(run_id) if run_id else None
    r = r or service.run("baseline")
    try:
        return {"profile": service.district_profile(code), "trace": service.trace(r, code)}
    except KeyError:
        raise HTTPException(404, "district not found")


@app.get("/api/district/{code}/candidates")
def district_candidates(code: int, run_id: Optional[str] = None, year: Optional[int] = None):
    r = (service.get(run_id) if run_id else None) or service.run("baseline")
    years = [int(k["year"]) for k in r["kpis"]]
    if year is not None and year not in years:
        raise HTTPException(400, f"year must be one of {years}")
    out = service.candidates(r, code, year)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.get("/api/trade")
def trade_dependencies(product: Optional[str] = None):
    """Who India buys pulses and edible oils from (latest complete fiscal year, India Data Portal / DGCI&S)."""
    return trade.suppliers(product=product[:60] if product else None)


@app.get("/api/trade/decompose")
def trade_decompose(country: str, product: str, pct: float = 100.0):
    """A bilateral import cut -> volume to replace and hectares needed per substitute crop (no LP)."""
    try:
        return trade.decompose(country[:60], product[:60], max(0.0, min(float(pct), 100.0)))
    except ValueError as e:
        raise HTTPException(400, str(e))


class ChatRequest(BaseModel):
    messages: list = Field(max_length=100)  # only the last 12 turns are used
    language: str = Field("en", max_length=8)
    session_id: Optional[str] = Field(None, max_length=16)  # saved chat session to append to (new one if absent)
    context: Optional[dict] = None  # {"run_id": the plan on the user's screen (a run or a saved policy "p-...")}


class DocumentRequest(BaseModel):
    run_id: str = Field(..., max_length=16)
    state: Optional[str] = Field(None, max_length=80)
    language: str = Field("en", max_length=8)
    regenerate: bool = False
    instructions: str = Field("", max_length=4000)  # then held to documents.MAX_INSTRUCTIONS (422) after cleaning
    refresh_research: bool = False


def _doc_run(run_id: str) -> dict:
    if not service.valid_run_id(run_id):
        raise HTTPException(400, "invalid run id")
    r = service.get(run_id)
    if r is None:
        raise HTTPException(404, "this plan is no longer available - run the optimizer again")
    return r


def _doc_scope(state: Optional[str], language: str) -> tuple:
    try:
        scope = documents.valid_scope(state)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if language not in agent.LANGUAGES:
        raise HTTPException(400, "unsupported language")
    return scope, language


@app.get("/api/documents/{run_id}")
def get_document(run_id: str, state: Optional[str] = None, language: str = "en"):
    """The saved policy document for a plan (a run or a saved policy 'p-...'), or null if none was written yet."""
    if not service.valid_run_id(run_id):
        raise HTTPException(400, "invalid run id")
    scope, lang = _doc_scope(state, language)
    job = documents.running_job(run_id, scope, lang)
    return {"document": documents.load(run_id, scope, lang), "busy": documents.busy(run_id, scope, lang),
            "job": {"id": job.id} if job is not None else None, "scope": scope, "language": lang}


def _doc_quota(request: Request) -> None:
    """A new document version costs ~3 Gemini calls (writer + 2 grounded searches): 6 per visitor per 10 minutes,
    shared by the Policy document dialog and the co-pilot (30 when Gemini is off: the template costs no model call)."""
    _rate_limit(request, limit=6 if settings.gemini_enabled else 30, bucket="document")
    _gemini_quota(request)


@app.post("/api/documents")
def write_document(req: DocumentRequest, request: Request):
    """Write the policy document once (or return the saved one); regenerate=true writes a new version, optionally
    following `instructions`. Server-Sent Events: step progress, then the document."""
    if not service.valid_run_id(req.run_id):  # cheap checks before the plan is loaded
        raise HTTPException(400, "invalid run id")
    scope, lang = _doc_scope(req.state, req.language)
    try:
        instr = documents.clean_instructions(req.instructions)
    except ValueError as e:
        raise HTTPException(422, str(e))
    r = _doc_run(req.run_id)
    regenerate = bool(req.regenerate or instr or req.refresh_research)
    running = documents.running_job(r["run_id"], scope, lang)
    if running is not None:  # someone (or the co-pilot) is writing it right now: follow that job
        note = ([{"type": "notice", "text": "This document was already being written, so your changes were not "
                                            "applied. Submit them again when this version is ready."}]
                if instr or req.refresh_research else None)
        return _stream(running, prefix=note)
    if not regenerate:
        old = documents.load(r["run_id"], scope, lang)
        if old is not None:
            return _stream_events([{"type": "document", "document": documents.public(old), "reused": True},
                                   {"type": "done"}])
    _doc_quota(request)  # only a new version costs quota
    try:
        job, _ = documents.write_job(r, scope, lang, regenerate, instr, req.refresh_research, source="ui",
                                     owner=_client_ip(request))
    except jobs.Busy as e:
        raise HTTPException(429, str(e))
    return _stream(job)


@app.get("/api/import-check/{run_id}")
def import_check(run_id: str, state: Optional[str] = None):
    """Will the plan's extra production actually cut imports? (deterministic; no model call)"""
    r = _doc_run(run_id)
    scope, _ = _doc_scope(state, "en")
    return documents.import_check_cached(r, scope)


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _stream(job, after: int = 0, prefix: list | None = None) -> StreamingResponse:
    """A background job's events as Server-Sent Events (live, with heartbeats; resumable with ?after=<seq>)."""
    return StreamingResponse(jobs.sse(job, after=after, prefix=prefix), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


def _stream_events(events: list) -> StreamingResponse:
    async def gen():
        yield ": stream open\n\n"
        for ev in events:
            yield f"data: {json.dumps(ev, default=str)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, request: Request, after: int = 0):
    """Resume a co-pilot answer or a document write after a dropped connection: the events after `after` (the last
    `seq` the client saw), then live until the job is done."""
    if not jobs.valid_id(job_id):
        raise HTTPException(400, "invalid job id")
    _rate_limit(request, limit=240, window=600, bucket="resume")
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "This answer is no longer held on this server; it is in the saved chat history.")
    return _stream(job, after=max(0, min(int(after), 1_000_000)))


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str, request: Request):
    """Stop a co-pilot answer (Stop button). Policy documents are not stopped: they are saved when finished."""
    if not jobs.valid_id(job_id):
        raise HTTPException(400, "invalid job id")
    _rate_limit(request, limit=60, window=600, bucket="cancel")
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    if job.kind != "chat":
        raise HTTPException(400, "only co-pilot answers can be stopped")
    job.cancel()
    return {"id": job.id, "status": job.status, "stopping": not job.done}


_ACTIVITY_PHASES = {"plan", "retry", "wrapup", "fallback", "scope", "stopped"}


def _recorded(events, sid: str, question: str, language: str, turn: str | None = None):
    """Pass the co-pilot's events through and save the exchange to the shared chat session afterwards (with the
    activity feed, so a reopened chat shows how each answer was built). A policy document still being written when
    the answer ends is added to the saved message when it is ready."""
    yield {"type": "session", "id": sid}
    t0 = time.monotonic()
    acc: dict = {"content": [], "citations": [], "policies": [], "simulations": [], "documents": [], "model": None,
                 "error": None, "activity": [], "turn": turn}
    pending: list = []  # document jobs still running
    saved = False

    def save() -> None:
        if not question:
            return
        ans = {**acc, "content": "".join(acc["content"]).strip() or (acc["error"] or "(no answer)"),
               "elapsed_s": round(time.monotonic() - t0, 1)}
        try:
            chats.record(sid, question, ans, language)
        except Exception as e:  # pragma: no cover - never break an answer because the history store failed
            print(f"[chats] session {sid} not saved: {type(e).__name__}: {e}")

    def finished_documents():
        """Document jobs that ended during the answer: show them now; the others are added to the chat later."""
        for jid in list(pending):
            j = jobs.get(jid)
            if j is not None and j.done and isinstance(j.result, dict):
                pending.remove(jid)
                ref = {k: j.result.get(k) for k in ("run_id", "scope", "language", "version", "title", "policy_name")}
                acc["documents"].append(ref)
                yield {"type": "document", **ref, "created": not j.result.get("reused")}

    def later(jid: str) -> None:
        j = jobs.get(jid)
        if j is not None and turn:
            j.on_finish(lambda job: isinstance(job.result, dict) and chats.add_document(sid, turn, job.result))

    try:
        for ev in events:
            t = ev.get("type")
            if t == "done" and saved:
                continue
            if t == "done":  # store the exchange first: "done" stays the last event of the stream
                yield from finished_documents()
                save()
                saved = True
                for jid in pending:
                    later(jid)
                yield {"type": "session", "id": sid, "saved": True}
            elif t == "text":
                acc["content"].append(str(ev.get("text", "")))
            elif t == "text_reset":
                acc["content"] = []
            elif t == "citations":
                acc["citations"] = ev.get("citations") or []
            elif t == "policy":
                acc["policies"].append({"id": ev.get("id"), "name": ev.get("name")})
            elif t == "simulation":
                acc["simulations"].append(ev.get("run_id"))
            elif t == "document":
                acc["documents"].append({k: ev.get(k) for k in ("run_id", "scope", "language", "title", "version")})
            elif t == "doc_job" and jobs.valid_id(ev.get("id")):
                pending.append(ev["id"])
            elif t == "model":
                acc["model"] = ev.get("model")
            elif t == "error":
                acc["error"] = ev.get("text")
            elif t == "tool_call":
                acc["activity"].append({"id": ev.get("id"), "text": ev.get("label") or "Gathering information"})
            elif t == "tool_result":
                for a in reversed(acc["activity"]):
                    if a.get("id") == ev.get("id") and "ok" not in a:
                        a.update(ok=bool(ev.get("ok", True)), detail=str(ev.get("summary") or "")[:200])
                        break
            elif t == "status" and ev.get("phase") in _ACTIVITY_PHASES:
                acc["activity"].append({"text": ev.get("text"), "kind": ev.get("phase")})
            yield ev
        if not saved:
            yield from finished_documents()
            save()
            saved = True
            for jid in pending:
                later(jid)
            yield {"type": "session", "id": sid, "saved": True}
    finally:
        if not saved:  # stopped mid-answer: keep what was said so far
            save()


@app.post("/api/agent/chat")
def chat(req: ChatRequest, request: Request):
    _rate_limit(request)
    _gemini_quota(request)
    msgs = [m for m in req.messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-12:]
    for m in msgs:
        m["content"] = str(m.get("content", ""))[:4000]
    lang = req.language if req.language in agent.LANGUAGES else "en"
    rid = (req.context or {}).get("run_id")
    ctx = {"run_id": rid} if service.valid_run_id(rid) else {}
    sid = req.session_id if chats.valid_id(req.session_id) else chats.new_id()
    question = msgs[-1]["content"] if msgs and msgs[-1]["role"] == "user" else ""
    ip = _client_ip(request)

    def work(job):
        # what-ifs the co-pilot runs count against this visitor's simulation limit, like the UI's own solves
        events = agent.chat(msgs, lang, on_new_solve=lambda: _solve_quota(request), context=ctx,
                            on_doc_write=lambda: _doc_quota(request), cancelled=lambda: job.cancelled, owner=ip)
        for ev in _recorded(events, sid, question, lang, turn=job.id):
            job.emit(ev)

    # the answer runs as a background job: a dropped connection resumes it (GET /api/jobs/{id}/events?after=N)
    # instead of losing it, and the Stop button cancels it (POST /api/jobs/{id}/cancel)
    try:
        job, _ = jobs.start("chat", work, owner=ip)
    except jobs.Busy as e:
        raise HTTPException(429, str(e))
    return _stream(job)


# ---- saved policies & chat sessions (shared by everyone using this deployment; no login)
class PolicySave(BaseModel):
    run_id: str = Field(..., max_length=16)
    name: str = Field(..., max_length=200)
    note: str = Field("", max_length=2000)


class PolicyPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    note: Optional[str] = Field(None, max_length=2000)
    deleted: Optional[bool] = None


def _write_quota(request: Request) -> None:
    _rate_limit(request, limit=60, window=600, bucket="store")


def _store_error(e: Exception) -> HTTPException:
    print(f"[store] {type(e).__name__}: {e}")
    return HTTPException(503, "The policy library is unavailable right now; please retry.")


@app.get("/api/policies")
def list_policies():
    try:
        rows = policies.list_all()
    except Exception as e:
        raise _store_error(e)
    return {"policies": rows, "max": policies.MAX_POLICIES, "store": persist.status()}


@app.post("/api/policies")
def save_policy(req: PolicySave, request: Request):
    if not service.valid_run_id(req.run_id):
        raise HTTPException(400, "invalid run id")
    _write_quota(request)
    res = service.get(req.run_id)
    if res is None:
        raise HTTPException(404, "this plan is no longer available - run the optimizer again, then save")
    try:
        return policies.save(res, req.name, req.note, source="user")
    except OverflowError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise _store_error(e)


@app.get("/api/policies/{pid}")
def get_policy(pid: str):
    m = policies.get_meta(pid)
    if not m:
        raise HTTPException(404, "policy not found")
    return m


@app.patch("/api/policies/{pid}")
def patch_policy(pid: str, req: PolicyPatch, request: Request):
    if not policies.POLICY_ID.match(pid):
        raise HTTPException(404, "policy not found")
    _write_quota(request)
    m = policies.update(pid, name=req.name, note=req.note, deleted=req.deleted)
    if not m:
        raise HTTPException(404, "policy not found")
    return m


@app.delete("/api/policies/{pid}")
def delete_policy(pid: str, request: Request, purge: bool = False):
    """Soft delete (restorable from the Deleted tab); purge=true removes a deleted policy and its snapshot for good."""
    if not policies.POLICY_ID.match(pid):
        raise HTTPException(404, "policy not found")
    _write_quota(request)
    if purge:
        m = policies.get_meta(pid)
        if not m:
            raise HTTPException(404, "policy not found")
        if not m.get("deleted"):
            # the library has no login, so one request must never wipe a live policy
            raise HTTPException(409, "Move the policy to Deleted first; only deleted policies can be removed for good.")
        if not policies.purge(pid):
            raise HTTPException(404, "policy not found")
        return {"id": pid, "purged": True}
    m = policies.update(pid, deleted=True)
    if not m:
        raise HTTPException(404, "policy not found")
    return m


@app.get("/api/chats")
def list_chats():
    try:
        rows = chats.list_all()
    except Exception as e:
        raise _store_error(e)
    return {"chats": rows, "store": persist.status()}


@app.get("/api/chats/{cid}")
def get_chat(cid: str):
    d = chats.get(cid)
    if not d:
        raise HTTPException(404, "chat not found")
    return d


@app.delete("/api/chats/{cid}")
def delete_chat(cid: str, request: Request):
    if not chats.valid_id(cid):
        raise HTTPException(404, "chat not found")
    _write_quota(request)
    if not chats.delete(cid):
        raise HTTPException(404, "chat not found")
    return {"id": cid, "deleted": True}


@app.get("/api/forecast")
def forecast(request: Request):
    if not settings.gemini_enabled:
        raise HTTPException(503, "Gemini not configured")
    _rate_limit(request, limit=10, bucket="forecast")
    if not gemini.outlook_cached():
        _gemini_quota(request)
    try:
        return agent.run_gemini_outlook()
    except Exception as e:
        raise HTTPException(503, f"The Gemini outlook is unavailable right now ({type(e).__name__}); please retry.")


# ---- static SPA
if os.path.isdir(settings.static_dir):
    assets = os.path.join(settings.static_dir, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    _static_root = os.path.realpath(settings.static_dir)

    @app.get("/{path:path}")
    def spa(path: str):
        fp = os.path.realpath(os.path.join(_static_root, path))
        if path and os.path.isfile(fp) and os.path.commonpath([fp, _static_root]) == _static_root:
            return FileResponse(fp)
        return FileResponse(os.path.join(_static_root, "index.html"), headers={"Cache-Control": "no-cache"})
