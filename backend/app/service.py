"""Simulation service: caching, the shared run store, public serialisation, BigQuery decision-trace logging,
read-only SQL."""
from __future__ import annotations

import contextvars
import gzip
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict
from functools import lru_cache

import numpy as np
import pandas as pd

from . import store as st
from .config import settings
from .data import OPTIONAL_TABLES, TABLES, store
from .optimizer import SimParams, district_candidates, district_trace, simulate
from .scenarios import scenario_params

_cache: "OrderedDict[str, dict]" = OrderedDict()
_pinned: "set[str]" = set()  # preset scenarios (no overrides) are never evicted, so clicking one never re-solves
_inflight: "dict[str, threading.Event]" = {}
_lock = threading.Lock()
MAX_CACHED_RUNS = 24  # custom runs kept in memory besides the pinned presets (all of them stay in the run store)
# set by the co-pilot for the duration of one conversation: called before every solve that is neither cached nor
# already in flight, so tool-triggered what-ifs count against the same limits as /api/simulate (it raises to refuse)
_new_solve_hook: contextvars.ContextVar = contextvars.ContextVar("agrisus_new_solve_hook", default=None)
RUN_ID = re.compile(r"^[0-9a-f]{12}$")
POLICY_RUN_ID = re.compile(r"^p-[a-z0-9]{10}$")  # a saved policy's snapshot, opened without re-solving
_solve_seconds: list = []  # recent solve durations, for the progress estimate shown while the optimizer runs


def set_new_solve_hook(fn):
    return _new_solve_hook.set(fn)


def valid_run_id(run_id) -> bool:
    return isinstance(run_id, str) and bool(RUN_ID.match(run_id) or POLICY_RUN_ID.match(run_id))


def _key(sp: SimParams) -> str:
    # keyed on the *effective* parameters, so "baseline" and "baseline + overrides equal to the defaults" share a run
    return hashlib.sha1(json.dumps(asdict(sp), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _params(scenario: str, overrides: dict | None) -> SimParams:
    params = scenario_params(scenario, overrides)
    allowed = set(SimParams.__dataclass_fields__)
    return SimParams(**{a: b for a, b in params.items() if a in allowed})


@lru_cache(maxsize=1)
def model_version() -> str:
    """Fingerprint of everything that determines a solve besides its parameters (model code, data, yield engine,
    climate source): stored runs are only reused by a deployment with the same fingerprint."""
    h = hashlib.sha1()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("optimizer.py", "data.py", "scenarios.py", "trade.py", "yield_model.py"):
        with open(os.path.join(here, name), "rb") as f:
            h.update(f.read())
    ds = store()
    for name in sorted(ds._t):  # the tables as loaded (BigQuery or the bundled snapshot), not the files on disk
        df = ds._t[name]
        h.update(name.encode())
        try:
            h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
        except TypeError:  # pragma: no cover - unhashable cells
            h.update(df.to_csv(index=False).encode())
    for root, dirs, files in os.walk(settings.model_dir):
        dirs.sort()
        for fn in sorted(files):
            h.update(fn.encode())
            with open(os.path.join(root, fn), "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    h.update(json.dumps([settings.data_backend, settings.bq_dataset, settings.climate_table, ds.data_source,
                         ds.climate_source, ds.yield_engine.get("active")], default=str).encode())
    return h.hexdigest()[:10]


# ---- run snapshots: the public result (gzipped JSON) + the district x crop frame (Parquet) the district cards need
def _int_keys(x):
    """JSON turns the year-keyed dicts (districts[].years) into string keys; restore the ints the code expects."""
    if isinstance(x, dict):
        if x and all(isinstance(k, str) and k.isdigit() for k in x):
            return {int(k): _int_keys(v) for k, v in x.items()}
        return {k: _int_keys(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_int_keys(v) for v in x]
    return x


def public(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def public_gzip(res: dict) -> bytes:
    """The public result as gzipped JSON, serialised once per run (the UI fetches the same 2.5 MB plan repeatedly)."""
    gz = res.get("_gz")
    if gz is None:
        gz = gzip.compress(json.dumps(public(res), separators=(",", ":"), default=str).encode(), 6)
        res["_gz"] = gz
    return gz


def snapshot_put(prefix: str, res: dict) -> None:
    """Write a run under prefix (frame first: the JSON's presence marks a complete snapshot)."""
    b = io.BytesIO()
    res["_frame"].to_parquet(b, compression="zstd", index=False)
    blobs = st.blobs()
    blobs.put(f"{prefix}.parquet", b.getvalue(), "application/vnd.apache.parquet")
    blobs.put(f"{prefix}.json.gz", public_gzip(res), "application/gzip")


def snapshot_get(prefix: str) -> dict | None:
    blobs = st.blobs()
    raw = blobs.get(f"{prefix}.json.gz")
    if not raw:
        return None
    fr = blobs.get(f"{prefix}.parquet")
    if not fr:
        return None
    res = _int_keys(json.loads(gzip.decompress(raw)))
    res["_frame"] = pd.read_parquet(io.BytesIO(fr))
    res["_gz"] = raw
    return res


def _run_prefix(run_id: str) -> str:
    return f"runs/{model_version()}/{run_id}"


def _load_stored(run_id: str) -> dict | None:
    if not settings.persist_runs or not RUN_ID.match(run_id or ""):
        return None
    try:
        return snapshot_get(_run_prefix(run_id))
    except Exception as e:  # the store is an accelerator: never fail a request because of it
        print(f"[store] run {run_id} not loaded: {type(e).__name__}: {e}")
        return None


def _persist(res: dict) -> None:
    if not settings.persist_runs:
        return
    try:
        snapshot_put(_run_prefix(res["run_id"]), res)
    except Exception as e:  # pragma: no cover
        print(f"[store] run {res.get('run_id')} not persisted: {type(e).__name__}: {e}")


def _register(k: str, res: dict, pin: bool = False) -> dict:
    with _lock:
        if k in _cache:  # a concurrent load/solve won: keep one object per run
            _cache.move_to_end(k)
            return _cache[k]
        _cache[k] = res
        if pin:
            _pinned.add(k)
        evictable = [x for x in _cache if x not in _pinned]
        for old in evictable[:max(0, len(evictable) - MAX_CACHED_RUNS)]:
            del _cache[old]
    return res


def register(res: dict) -> dict:
    """Make a loaded snapshot (e.g. a saved policy) available to the district cards, policy document and co-pilot."""
    return _register(res["run_id"], res)


def peek(scenario: str = "baseline", overrides: dict | None = None) -> dict | None:
    """Result for these parameters without solving: memory, then the shared run store (both free, not rate-limited)."""
    k = _key(_params(scenario, overrides))
    with _lock:
        hit = _cache.get(k)
    if hit is not None:
        return hit
    res = _load_stored(k)
    return _register(k, res, pin=not overrides) if res is not None else None


def run(scenario: str = "baseline", overrides: dict | None = None) -> dict:
    sp = _params(scenario, overrides)
    k = _key(sp)
    hook = _new_solve_hook.get()
    while True:  # one solve per parameter set; concurrent identical requests wait for it
        with _lock:
            if k in _cache:
                _cache.move_to_end(k)
                return _cache[k]
            ev = _inflight.get(k)
            owner = ev is None
            if owner:
                ev = _inflight[k] = threading.Event()
        if owner:
            break
        ev.wait(timeout=600)
    fresh = False
    try:
        res = _load_stored(k)  # solved before: by another instance, or by this one before a restart
        if res is None:
            if hook is not None:
                hook()  # may raise (limit reached); waiting requests then retry under their own limits
            res = simulate(sp)
            res["run_id"] = k
            res["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            fresh = True
            secs = (res.get("solver") or {}).get("seconds")
            if secs is not None:
                _solve_seconds.append(float(secs))
                del _solve_seconds[:-8]
        res = _register(k, res, pin=not overrides)
    finally:
        with _lock:
            _inflight.pop(k, None)
        ev.set()
    if fresh:
        threading.Thread(target=_persist, args=(res,), daemon=True).start()
        if settings.project and settings.write_traces_to_bq:
            threading.Thread(target=_log_bq, args=(res,), daemon=True).start()
    return res


def get(run_id: str) -> dict | None:
    """A run by id: memory, the shared run store, or a saved policy's snapshot (ids are validated first)."""
    if not valid_run_id(run_id):
        return None
    with _lock:
        hit = _cache.get(run_id)
    if hit is not None:
        return hit
    if POLICY_RUN_ID.match(run_id):
        from . import policies
        res = policies.load_run(run_id[2:])
    else:
        res = _load_stored(run_id)
    return _register(run_id, res) if res is not None else None


def solve_estimate_s() -> float:
    """Typical solve time on this instance (median of recent solves), for the UI's progress bar."""
    xs = sorted(_solve_seconds)
    return round(xs[len(xs) // 2], 1) if xs else 45.0


def forget(run_id: str) -> None:
    with _lock:
        _cache.pop(run_id, None)
        _pinned.discard(run_id)


def trace(res: dict, district_code: int) -> dict:
    return district_trace(res, district_code)


def candidates(res: dict, district_code: int, year: int | None = None) -> dict:
    return district_candidates(res, district_code, year)


def ledger_rows(res: dict) -> list:
    """District x year budget ledger (BigQuery `policy_budget_ledger`): where the money goes and what it returns."""
    p = res["params"]
    out = []
    for d in res["districts"]:
        for y, v in d["years"].items():
            if v["incentive_cr"] < 0.005 and v["area_shifted_ha"] < 1:
                continue
            o, rt = v["outlay_cr"], v["returns_cr"]
            out.append({"run_id": res["run_id"], "created_at": res["created_at"], "scenario": p["scenario"],
                        "mode": p.get("budget_mode", "fixed"), "agri_year": int(y), "state_name": d["state_name"],
                        "district_code": int(d["district_code"]), "district_name": d["district_name"],
                        "dbt_cr": o["dbt"], "seed_kit_cr": o["seed_kits"], "price_deficiency_cr": o["price_support"],
                        "total_outlay_cr": v["incentive_cr"], "area_shifted_ha": v["area_shifted_ha"],
                        "water_saved_mcm": v["water_saved_mcm"], "import_value_saved_cr": rt["forex"],
                        "power_subsidy_saved_cr": rt["power_subsidy"], "urea_subsidy_saved_cr": rt["urea_subsidy"],
                        "mropi": v["mropi"], "tier": v["mropi_tier"]})
    return out


def _log_bq(res: dict) -> None:
    """Append the run's decision trace to BigQuery `simulation_decision_traces` (best effort)."""
    try:
        from google.cloud import bigquery
        bq = bigquery.Client(project=settings.project)
        f = res["_frame"]
        f = f[(f.plan_area_ha - f.baseline_area_ha).abs() >= 1]
        rows = [{
            "run_id": res["run_id"], "trace_id": str(uuid.uuid4()), "created_at": res["created_at"],
            "scenario": res["params"]["scenario"], "district_code": int(r.district_code), "state_name": r.state_name,
            "agri_year": int(r.agri_year), "season": r.season, "crop": r.crop,
            "baseline_area_ha": float(r.baseline_area_ha), "plan_area_ha": float(r.plan_area_ha),
            "predicted_yield_kg_ha": float(r.yield_kg_ha), "net_margin_inr_ha": float(r.net_margin_inr_ha),
            "cwar": float(r.cwar), "reduced_cost": float(r.reduced_cost_cr_per_kha),
            "params_json": json.dumps(res["params"], default=str),
        } for r in f.itertuples()]
        table = f"{settings.project}.{settings.bq_dataset}.simulation_decision_traces"
        for i in range(0, len(rows), 5000):
            errs = bq.insert_rows_json(table, rows[i:i + 5000])
            if errs:
                print("[bq] insert errors", errs[:2])
                break
        kp = [{"run_id": res["run_id"], "created_at": res["created_at"], "scenario": res["params"]["scenario"],
               "agri_year": k["year"], "kpis_json": json.dumps(k)} for k in res["kpis"]]
        bq.insert_rows_json(f"{settings.project}.{settings.bq_dataset}.simulation_runs", kp)
        led = ledger_rows(res)
        for i in range(0, len(led), 5000):
            errs = bq.insert_rows_json(f"{settings.project}.{settings.bq_dataset}.policy_budget_ledger", led[i:i + 5000])
            if errs:
                print("[bq] ledger insert errors", errs[:2])
                break
    except Exception as e:  # pragma: no cover
        print("[bq] trace logging skipped:", e)


_FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|call|export|load|"
                        r"attach|detach|install|copy|pragma|set|reset|information_schema|__tables__)\b", re.I)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+(`[^`]+`|[\w.\-]+)", re.I)
_CTE = re.compile(r"\b(\w+)\s+as\s*\(", re.I)
_duck = None
# functions that reach outside the dataset or bill other services (federated queries, vector search over arbitrary
# tables, change history, file readers in DuckDB); namespaced functions (AI.*, ML.*, KEYS.*, `proj.ds.udf`) are
# refused separately, only SAFE.<function> is allowed
_DENIED_FUNCTIONS = {"external_query", "external_object_transform", "vector_search", "session_user", "appends",
                     "changes", "glob", "query", "query_table", "sniff_csv", "parquet_scan", "iceberg_scan",
                     "delta_scan"}
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9][A-Za-z0-9_]*(?:\.[0-9]*(?:[eE][+-]?[0-9]+)?)?")


def _string_end(q: str, i: int, bigquery: bool) -> int:
    """Index just past the string literal that starts at q[i], or -1 if it is not terminated."""
    quote = q[i]
    if bigquery and q.startswith(quote * 3, i):  # GoogleSQL triple-quoted string
        j = i + 3
        while j < len(q):
            if q[j] == "\\":
                j += 2
            elif q.startswith(quote * 3, j):
                return j + 3
            else:
                j += 1
        return -1
    j = i + 1
    while j < len(q):
        if bigquery and q[j] == "\\":  # GoogleSQL escapes (raw strings too: a backslash never ends the literal)
            j += 2
        elif q[j] == quote:
            if not bigquery and q.startswith(quote * 2, j):  # DuckDB doubles the quote instead
                j += 2
                continue
            return j + 1
        else:
            j += 1
    return -1


def _function_gate(q: str, bigquery: bool) -> str | None:
    """Lex the statement (comments, string literals, quoted identifiers) and refuse calls to namespaced or denied
    functions, e.g. AI.GENERATE / ML.GENERATE_TEXT (would bill Vertex AI through BigQuery), EXTERNAL_QUERY,
    VECTOR_SEARCH or a quoted `project.dataset.udf`(). Returns the reason, or None if the statement may run."""
    if not bigquery and "\\" in q:  # DuckDB E'...' strings would read escapes differently from this lexer
        return "backslashes are not allowed"
    toks: list = []  # (kind, text): ident | qident | . | ( | other
    i, n = 0, len(q)
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1
        elif q.startswith("--", i):
            j = q.find("\n", i)
            i = n if j < 0 else j + 1
        elif q.startswith("/*", i):
            j = q.find("*/", i + 2)
            if j < 0:
                return "unterminated comment"
            i = j + 2
        elif c == "`" or (c == '"' and not bigquery):  # quoted identifier (DuckDB uses "...")
            j = i + 1
            while j < n and q[j] != c:
                j += 2 if (c == "`" and q[j] == "\\") else 1
            if j >= n:
                return "unterminated quoted identifier"
            toks.append(("qident", q[i + 1:j]))
            i = j + 1
        elif c in "'\"":
            j = _string_end(q, i, bigquery)
            if j < 0:
                return "unterminated string literal"
            toks.append(("other", "''"))
            i = j
        elif c in "#$\\@":  # BigQuery '#' comments / legacy-SQL switch, DuckDB $$ strings, parameters: never needed
            return f"'{c}' is only allowed inside string literals"
        elif c in ".(":
            toks.append((c, c))
            i += 1
        else:
            m = _IDENT.match(q, i) or _NUMBER.match(q, i)
            if m:
                toks.append(("ident" if c.isalpha() or c == "_" else "other", m.group(0)))
                i = m.end()
            else:
                toks.append(("other", c))
                i += 1
    for k, (kind, _) in enumerate(toks):
        if kind != "(" or k == 0 or toks[k - 1][0] not in ("ident", "qident"):
            continue
        path, j = [toks[k - 1]], k - 2
        while j >= 1 and toks[j][0] == "." and toks[j - 1][0] in ("ident", "qident"):
            path.insert(0, toks[j - 1])
            j -= 2
        if any(p[0] == "qident" for p in path):
            return "quoted function names are not allowed"
        names = [p[1].lower() for p in path]
        if len(names) == 2 and names[0] == "safe":
            names = names[1:]
        if len(names) > 1:
            return f"function {'.'.join(names).upper()} is not allowed (only standard SQL functions)"
        if names[0] in _DENIED_FUNCTIONS or names[0].startswith("read_"):
            return f"function {names[0].upper()} is not allowed"
    return None


# written by the app / created by bigquery/*.sql on Google Cloud only (not in the local snapshot)
BQ_ONLY_TABLES = {
    "simulation_runs": ["run_id", "created_at", "scenario", "agri_year", "kpis_json"],
    "policy_budget_ledger": ["run_id", "created_at", "scenario", "mode", "agri_year", "state_name", "district_code",
                             "district_name", "dbt_cr", "seed_kit_cr", "price_deficiency_cr", "total_outlay_cr",
                             "area_shifted_ha", "water_saved_mcm", "import_value_saved_cr", "power_subsidy_saved_cr",
                             "urea_subsidy_saved_cr", "mropi", "tier"],
    "simulation_decision_traces": ["run_id", "trace_id", "created_at", "scenario", "district_code", "state_name",
                                   "agri_year", "season", "crop", "baseline_area_ha", "plan_area_ha",
                                   "predicted_yield_kg_ha", "net_margin_inr_ha", "cwar", "reduced_cost", "params_json"],
    "trade_supplier_concentration": ["fiscal_year", "commodity_group", "product", "imports_kt", "imports_usd_mn",
                                     "hhi", "top_suppliers"],
    # one row per section per policy-document version (written by documents._log_bq)
    "policy_documents": ["doc_id", "run_id", "policy_id", "policy_name", "scope", "language", "version", "created_at",
                         "generator", "model", "title", "section_id", "section_title", "section_order", "markdown",
                         "instructions", "import_verdicts_json"],
}


def _on_bigquery() -> bool:
    # if the startup reads failed (e.g. no dataset access), SQL runs on the snapshot the app is actually using
    return bool(settings.project) and settings.data_backend == "bigquery" and store().bq_ok


def _queryable(local_only: bool = False) -> list:
    ds = store()
    local = TABLES + [t for t in OPTIONAL_TABLES if ds.has(t)]
    return local if local_only or not _on_bigquery() else local + [t for t in BQ_ONLY_TABLES if t not in local]


def _tables_allowed(q: str) -> bool:
    """Every FROM/JOIN target must be an Agri Sus table (optionally dataset/project-qualified) or a CTE."""
    ctes = {c.lower() for c in _CTE.findall(q)}
    ok = {t.lower() for t in _queryable()} | ctes
    ds = settings.bq_dataset.lower()
    proj = (settings.project or "").lower()
    for ref in _TABLE_REF.findall(q):
        parts = ref.strip("`").lower().split(".")
        if parts[-1] not in ok:
            return False
        if len(parts) >= 2 and parts[-2] != ds:
            return False
        if len(parts) == 3 and parts[0] != proj:
            return False
        if len(parts) > 3:
            return False
    # comma-joins: any other qualified identifier pointing outside our dataset is rejected too
    for m in re.findall(r"`([^`]+)`", q):
        if "." in m and m.lower().split(".")[-2] != ds:
            return False
    return True


def _duck_conn():
    """In-memory DuckDB holding copies of the snapshot tables, with file/network access disabled and locked."""
    import duckdb
    con = duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})
    ds = store()
    for t in _queryable(local_only=True):
        con.register("_src", ds[t])
        con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM _src')
        con.unregister("_src")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def _dry_run_refusal(job) -> str | None:
    """BigQuery's own parse of the statement (dry run, free): it must be a SELECT reading only this dataset."""
    st = (getattr(job, "statement_type", None) or "").upper()
    if st and st != "SELECT":
        return f"{st} statements are not allowed"
    proj, ds = (settings.project or "").lower(), settings.bq_dataset.lower()
    for t in getattr(job, "referenced_tables", None) or []:
        if (t.project or "").lower() != proj or (t.dataset_id or "").lower() != ds:
            return "only the Agri Sus dataset can be queried"
    return None


def sql(query: str, limit: int = 200) -> dict:
    """Read-only SQL over the Agri Sus tables: BigQuery when configured, otherwise DuckDB over the local snapshot."""
    q = query.strip().rstrip(";")
    if not re.match(r"^\s*(select|with)\b", q, re.I) or _FORBIDDEN.search(q) or ";" in q:
        return {"error": "only a single read-only SELECT statement is allowed"}
    on_bq = _on_bigquery()
    why = _function_gate(q, bigquery=on_bq)
    if why:
        return {"error": f"query refused: {why}"}
    if not _tables_allowed(q):
        return {"error": "only the Agri Sus tables can be queried: " + ", ".join(_queryable())}
    if on_bq:
        from google.cloud import bigquery
        bq = bigquery.Client(project=settings.project)
        base = {"default_dataset": f"{settings.project}.{settings.bq_dataset}", "use_legacy_sql": False}
        try:
            why = _dry_run_refusal(bq.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False,
                                                                                  **base)))
            if why:
                return {"error": f"query refused: {why}"}
            cfg = bigquery.QueryJobConfig(maximum_bytes_billed=2 * 10 ** 8, job_timeout_ms=30_000,
                                          labels={"app": "agri-sus", "source": "copilot-sql"}, **base)
            df = bq.query(q, job_config=cfg).result(timeout=45, max_results=limit).to_dataframe().head(limit)
        except Exception as e:
            return {"error": f"BigQuery rejected the query ({type(e).__name__})"}
        engine = "BigQuery"
    else:
        global _duck
        if _duck is None:
            _duck = _duck_conn()
        try:
            df = _duck.cursor().execute(q).fetchdf().head(limit)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"}
        engine = "DuckDB (local snapshot of the BigQuery dataset)"
    df = df.replace({np.nan: None})
    return {"engine": engine, "rows": json.loads(df.to_json(orient="records", default_handler=str)),
            "row_count": len(df)}


def schema() -> dict:
    ds = store()
    out = {t: list(ds[t].columns) for t in _queryable(local_only=True)}
    if _on_bigquery():
        out.update({t: c for t, c in BQ_ONLY_TABLES.items() if t not in out})
    return out


def find_district(q, min_score: int = 75) -> int | None:
    ctx = store().context
    try:
        code = int(q)
        return code if code in set(ctx.district_code) else None
    except (TypeError, ValueError):
        pass
    from rapidfuzz import process, fuzz
    names = ctx.district_name.str.lower().tolist()
    m = process.extractOne(str(q).lower().replace("district", "").strip(), names, scorer=fuzz.ratio if min_score >= 90 else fuzz.WRatio)
    if m and m[1] >= min_score:
        return int(ctx.district_code.iloc[m[2]])
    return None


def district_profile(code: int) -> dict:
    ds = store()
    ctx = ds.context.set_index("district_code").loc[code]
    base = ds["baseline_crop_area_district"]
    b = base[base.district_code == code].sort_values("area_ha", ascending=False).head(10)
    keys = ["state_name", "district_name", "net_sown_area_ha", "irrigation_coverage_pct", "groundwater_share",
            "canal_share", "current_fallow_ha", "soil_n_kg_ha_est", "soil_oc_pct_est", "zinc_deficient_share",
            "cgwb_stage_of_extraction_pct", "cgwb_category", "cgwb_stage_change_2022_24_pp", "gw_pre_monsoon_depth_m",
            "gw_depth_trend_m_per_yr", "safe_gw_allowance_mm_yr", "current_gw_irrigation_mm_yr",
            "micro_irrigation_share", "clim_kharif_rain_mm", "clim_rabi_tmax_c", "clim_terminal_heat_days", "avg_holding_ha"]
    prof = {k: (None if pd.isna(ctx[k]) else (ctx[k].item() if hasattr(ctx[k], "item") else ctx[k])) for k in keys}
    prof["district_code"] = code
    prof["top_crops_2020_24"] = [{"crop": r.crop, "season": r.season, "area_ha": round(r.area_ha),
                                  "yield_kg_ha": round(r.yield_kg_ha) if pd.notna(r.yield_kg_ha) else None}
                                 for r in b.itertuples()]
    return prof
