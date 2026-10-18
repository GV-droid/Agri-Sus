"""Runtime configuration (environment variables; all optional for local/offline mode)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _first_existing(*paths: str) -> str:
    for p in paths:
        if os.path.isdir(p):
            return p
    return paths[0]


def _load_env_file(path: str | None = None) -> None:
    """Local runs: read KEY=VALUE lines from the project's .env (see .env.example) into the environment.
    Variables already set in the shell win. .env is never committed, uploaded to Cloud Build or baked into the image."""
    path = path or os.getenv("AGRISUS_ENV_FILE") or os.path.join(_ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (s.strip() for s in line.split("=", 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key and value and not os.environ.get(key):
                os.environ[key] = value


_load_env_file()  # before Settings: its defaults are read from the environment at import time


def _int_or_none(v: str | None) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None  # "", "default": keep the model's own setting


def _default_store_dir() -> str:
    # the project folder locally; inside the container the code folder is read-only for the app user
    return os.path.join(_ROOT, ".agrisus-store") if os.access(_ROOT, os.W_OK) else "/tmp/agrisus-store"


@dataclass
class Settings:
    # Data
    data_dir: str = os.getenv("AGRISUS_DATA_DIR", _first_existing(os.path.join(_ROOT, "data", "processed", "app"),
                                                                  "/app/data/app"))
    geo_path: str = os.getenv("AGRISUS_GEO_PATH", next(
        (p for p in [os.path.join(_ROOT, "data", "processed", "geo", "districts_india.geojson"),
                     "/app/data/geo/districts_india.geojson"] if os.path.exists(p)),
        os.path.join(_ROOT, "data", "processed", "geo", "districts_india.geojson")))
    model_dir: str = os.getenv("AGRISUS_MODEL_DIR", os.path.join(_ROOT, "backend", "models"))
    static_dir: str = os.getenv("AGRISUS_STATIC_DIR", _first_existing(os.path.join(_ROOT, "frontend", "dist"),
                                                                      "/app/static"))
    # Google Cloud
    project: str | None = os.getenv("GOOGLE_CLOUD_PROJECT")
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    bq_dataset: str = os.getenv("BQ_DATASET", "agri_sus_mvp")
    data_backend: str = os.getenv("DATA_BACKEND", "local")  # local | bigquery
    # climate projections: bundled CMIP6 table, or the Google Earth Engine pipeline's output (scripts/gee_climate.py)
    climate_table: str = os.getenv("CLIMATE_TABLE", "climate_projection_district_year")
    # yield climate factor: agronomic prior, BigQuery ML boosted-tree challenger, or auto (challenger only if it wins
    # the 2020-24 hold-out comparison written by bigquery/02_bqml_yield_model.sql)
    yield_model: str = os.getenv("YIELD_MODEL", "auto").lower()  # auto | prior | bqml
    write_traces_to_bq: bool = os.getenv("WRITE_TRACES_TO_BQ", "true").lower() == "true"
    # Gemini (Vertex AI by default; falls back to Gemini API key)
    use_vertex: bool = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    agent_model: str = os.getenv("GEMINI_AGENT_MODEL", "gemini-3.8-flash")
    forecast_model: str = os.getenv("GEMINI_FORECAST_MODEL", "gemini-3.8-flash")
    # tried in order if the primary model id is unavailable in the project/region
    fallback_models: list = field(default_factory=lambda: os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-2.5-flash").split(","))
    max_agent_steps: int = int(os.getenv("AGENT_MAX_STEPS", "8"))
    # Gemini 3 models think at length by default (about a minute per agent step on Vertex AI); a capped thinking
    # budget keeps each step to seconds. "default" = the model's own setting.
    thinking_budget: int | None = _int_or_none(os.getenv("GEMINI_THINKING_BUDGET", "1024"))
    gemini_timeout_s: float = float(os.getenv("GEMINI_TIMEOUT_S", "90"))  # per Gemini call
    # a co-pilot answer must finish well inside the Cloud Run request timeout; after this the offline analyst answers
    agent_deadline_s: float = float(os.getenv("AGENT_DEADLINE_S", "240"))
    # same-origin by default (the SPA is served by this app and Vite proxies /api in dev); add origins explicitly
    allowed_origins: list = field(default_factory=lambda: [o for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o])
    # interactive API docs (/docs, /redoc, /openapi.json) are off in production; API_DOCS=true turns them on
    api_docs: bool = os.getenv("API_DOCS", "false").lower() in ("true", "1", "yes")
    # per-instance ceiling on Gemini-backed requests (co-pilot answers, new briefs, outlooks) across all visitors,
    # on top of the per-visitor limits: caps the Vertex AI bill if many addresses are used at once
    gemini_requests_per_10min: int = int(os.getenv("GEMINI_MAX_REQUESTS_PER_10MIN", "200"))
    # Saved policies, chat sessions and solved runs: Firestore documents + a Cloud Storage bucket on Google Cloud
    # (deploy/deploy.sh creates both inside the free tier and sets STORE_BUCKET); a local folder otherwise
    store_bucket: str | None = os.getenv("STORE_BUCKET") or None
    doc_store: str = os.getenv("DOC_STORE", "auto").lower()  # auto | firestore | gcs | local
    firestore_database: str = os.getenv("FIRESTORE_DATABASE", "(default)")
    store_dir: str = os.getenv("AGRISUS_STORE_DIR", _default_store_dir())
    # keep every solved run in the store, so restarts and other instances reuse it instead of re-solving
    persist_runs: bool = os.getenv("PERSIST_RUNS", "true").lower() in ("true", "1", "yes")

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key) or (self.use_vertex and bool(self.project))


settings = Settings()
