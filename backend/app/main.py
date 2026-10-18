"""FastAPI app: REST + SSE API and the compiled React UI (single Cloud Run service)."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import threading
import time
from collections import OrderedDict, defaultdict, deque
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from . import agent, gemini, service, trade
from .config import settings
from .data import PLAN_YEARS, store
from .optimizer import INSTRUMENTS
from .scenarios import BUDGET_MODES, MAX_TRADE_CUTS, SCENARIOS, sanitize_overrides, scenario_params

# policy levers exposed as UI controls; a scenario that presets one of them (e.g. gw_mode) tells the UI via /api/meta
LEVER_KEYS = {"max_shift_pct", "cereal_floor_pct", "gw_mode", "import_target_pct", "incentive_budget_cr",
              "rice_export_retention_pct", "price_realisation", "crop_growth_cap_pct", "budget_mode", "instruments",
              "trade_cuts"}
from .yield_model import METRICS_PATH

_SSE_PATHS = {"/api/agent/chat"}


class _GZipExceptStreams(GZipMiddleware):
    """gzip responses, except Server-Sent Events: compression buffers them, so the co-pilot's live steps would only
    reach the browser all at once when the answer is complete."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in _SSE_PATHS:
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
        for sc in SCENARIOS:  # baseline first, then pre-solve every crisis scenario so demo clicks are instant
            try:
                service.run(sc)
            except Exception as e:  # pragma: no cover
                print(f"[warm] {sc}: {e}")
    threading.Thread(target=w, daemon=True).start()


@app.get("/api/health")
def health():
    return {"status": "ok", "gemini": settings.gemini_enabled, "data_backend": settings.data_backend,
            "project": bool(settings.project)}


@app.get("/api/meta")
def meta():
    with open(METRICS_PATH) as f:
        metrics = json.load(f)
    return {
        "plan_years": PLAN_YEARS,
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
        if hit is not None:  # cached runs are free; only new solves count against the rate limit
            return service.public(hit)
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
    return service.public(service.run(req.scenario, req.overrides))


@app.get("/api/run/{run_id}")
def get_run(run_id: str):
    r = service.get(run_id)
    if not r:
        raise HTTPException(404, "run expired")
    return service.public(r)


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
    if year is not None and year not in PLAN_YEARS:
        raise HTTPException(400, f"year must be one of {PLAN_YEARS}")
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


class BriefRequest(BaseModel):
    run_id: Optional[str] = Field(None, max_length=40)
    state: Optional[str] = Field(None, max_length=80)
    language: str = Field("en", max_length=8)


_BRIEFS: "OrderedDict[tuple, dict]" = OrderedDict()
_BRIEFS_LOCK = threading.Lock()


@app.post("/api/brief")
def brief(req: BriefRequest, request: Request):
    r = (service.get(req.run_id) if req.run_id else None) or service.run("baseline")
    state = req.state if req.state in set(store().context.state_name) else None
    lang = req.language if req.language in agent.LANGUAGES else "en"
    key = (r["run_id"], state, lang)
    with _BRIEFS_LOCK:
        if key in _BRIEFS:  # the same brief again (re-opened modal): free, like a cached simulation
            _BRIEFS.move_to_end(key)
            return _BRIEFS[key]
    _rate_limit(request, limit=10, bucket="brief")
    _gemini_quota(request)
    out = agent.policy_brief(r, state, lang)
    if not (settings.gemini_enabled and out.get("model", "").startswith("template")):  # never cache a Gemini-failure fallback
        with _BRIEFS_LOCK:
            _BRIEFS[key] = out
            while len(_BRIEFS) > 64:
                _BRIEFS.popitem(last=False)
    return out


_SSE_END = object()


async def _sse(events, heartbeat: float = 10.0):
    """Server-Sent Events from a blocking generator (run in a thread). The response starts at once and a comment line
    goes out every `heartbeat` seconds while Gemini is thinking, so browsers and proxies never see a silent stream.
    An async generator, so a client disconnect cancels it here and the agent stops at its next step."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def put(ev) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        except RuntimeError:  # event loop closed: the server is shutting down
            stop.set()

    def pump() -> None:
        try:
            for ev in events:
                put(ev)
                if stop.is_set():  # the client went away: stop calling Gemini and tools
                    break
        except Exception as e:  # pragma: no cover
            put({"type": "error", "text": f"agent failed ({type(e).__name__})"})
        finally:
            put(_SSE_END)

    threading.Thread(target=pump, daemon=True, name="sse-pump").start()
    try:
        yield ": stream open\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if ev is _SSE_END:
                return
            yield f"data: {json.dumps(ev, default=str)}\n\n"
    finally:
        stop.set()


@app.post("/api/agent/chat")
def chat(req: ChatRequest, request: Request):
    _rate_limit(request)
    _gemini_quota(request)
    msgs = [m for m in req.messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-12:]
    for m in msgs:
        m["content"] = str(m.get("content", ""))[:4000]
    # what-ifs the co-pilot runs count against this visitor's simulation limit, like the UI's own solves
    events = agent.chat(msgs, req.language if req.language in agent.LANGUAGES else "en",
                        on_new_solve=lambda: _solve_quota(request))
    return StreamingResponse(_sse(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
