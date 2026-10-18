"""Saved policies: named, shared snapshots of an optimizer run - the levers that produced it plus every computed
result (national KPIs, 752 district plans, budget path, trade, stress tests and the district x crop frame behind the
district cards). Opening one re-renders it from the snapshot without re-running the optimizer.

Metadata lives in the document store (Firestore when available), snapshots in the blob store (Cloud Storage);
both fall back to local files for development. Deleting is a soft delete (restorable) until purged."""
from __future__ import annotations

import re
import threading

from . import service
from . import store as st
from .scenarios import SCENARIOS

POLICY_ID = re.compile(r"^[a-z0-9]{10}$")
MAX_POLICIES = 300  # saved + deleted; a demo-sized shared library, not an archive
NAME_MAX, NOTE_MAX = 80, 500
LEVERS = ("budget_mode", "incentive_budget_cr", "instruments", "import_target_pct", "max_shift_pct", "cereal_floor_pct",
          "gw_mode", "rice_export_retention_pct", "price_realisation", "crop_growth_cap_pct", "income_floor_pct",
          "import_price_mult", "trade_cuts", "states", "shocks", "horizon_years")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_lock = threading.Lock()
_listing = st.Listing("policies", order_by="created_at", limit=MAX_POLICIES + 50)


def clean(text, limit: int) -> str:
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(text or ""))).strip()[:limit]


def summary(res: dict) -> dict:
    """Headline numbers shown in the policy list (so listing never loads a snapshot)."""
    k = res["kpis"][-1]
    pm = res.get("policy_mix") or {}
    tot = pm.get("totals") or {}
    return {
        "first_year": res["kpis"][0]["year"], "end_year": k["year"],
        "assi": k.get("assi"), "assi_status_quo": k.get("assi_status_quo"),
        "import_saving_usd_bn": k.get("import_saving_usd_bn"), "water_saved_bcm": k.get("water_saved_bcm"),
        "farmer_income_vs_today_pct": k.get("farmer_income_vs_today_pct"),
        "budget_mode": pm.get("mode"), "target_met": pm.get("target_met"),
        "outlay_end_year_cr": k.get("incentive_cr"),
        "outlay_total_cr": tot.get("outlay_cr", sum(r.get("incentive_cr", 0) for r in res["kpis"])),
        "returns_total_cr": tot.get("returns_cr"),
        "districts": len(res.get("districts") or []),
    }


def _meta_public(m: dict) -> dict:
    return {k: m.get(k) for k in ("id", "name", "note", "source", "created_at", "updated_at", "deleted",
                                  "deleted_at", "scenario", "scenario_label", "run_id", "model_version", "levers",
                                  "summary")}


def get_meta(pid: str) -> dict | None:
    if not isinstance(pid, str) or not POLICY_ID.match(pid):
        return None
    m = st.docs().get("policies", pid)
    return _meta_public(m) if m else None


def list_all(include_deleted: bool = True) -> list:
    rows = [_meta_public(m) for m in _listing.rows()]
    return [r for r in rows if include_deleted or not r.get("deleted")]


def save(res: dict, name: str, note: str = "", source: str = "user") -> dict:
    """Snapshot a run under a new policy id. The run must carry its frame (any cached/loaded run does)."""
    name = clean(name, NAME_MAX) or "Untitled policy"
    note = clean(note, NOTE_MAX)
    if "_frame" not in res:
        raise ValueError("this run cannot be saved (no district frame)")
    run_ref = res.get("params_run_id") or res.get("run_id")
    with _lock:
        rows = _listing.rows()
        same = next((r for r in rows if not r.get("deleted") and r.get("name") == name and r.get("run_id") == run_ref),
                    None)
        if same:  # a double click / repeated co-pilot request: the policy already exists
            return _meta_public(same)
        if len(rows) >= MAX_POLICIES:
            raise OverflowError(f"the policy library is full ({MAX_POLICIES}); permanently delete some first")
        pid = st.new_id(10)
        prefix = f"policies/{pid}/run"
        service.snapshot_put(prefix, res)
        p = res.get("params") or {}
        scen = p.get("scenario") or "baseline"
        now = st.now_iso()
        meta = {
            "id": pid, "name": name, "note": note, "source": "copilot" if source == "copilot" else "user",
            "created_at": now, "updated_at": now, "deleted": False, "deleted_at": None,
            "scenario": scen, "scenario_label": (SCENARIOS.get(scen) or {}).get("label", scen),
            "run_id": run_ref, "model_version": service.model_version(),
            "levers": {k: p.get(k) for k in LEVERS if k in p},
            "summary": summary(res),
        }
        try:
            st.docs().put("policies", pid, meta)
        except Exception:
            st.blobs().delete_prefix(f"policies/{pid}/")
            raise
        _listing.bump()
    return _meta_public(meta)


def load_run(pid: str) -> dict | None:
    """The saved snapshot as a run (id 'p-<pid>'), with the policy's metadata attached for the UI banner."""
    meta = get_meta(pid)
    if not meta:
        return None
    res = service.snapshot_get(f"policies/{pid}/run")
    recomputed = res is None
    if res is None:
        res = _rebuild(pid, meta)
        if res is None:
            return None
    res = dict(res)
    res.pop("_gz", None)  # re-serialised: the run id and policy metadata below change the public JSON
    res["params_run_id"] = res.get("params_run_id") or res.get("run_id")
    res["run_id"] = f"p-{pid}"
    res["policy"] = {k: meta.get(k) for k in ("id", "name", "note", "source", "created_at", "deleted",
                                               "scenario_label")}
    if recomputed:
        res["policy"]["recomputed"] = True
    return res


def _rebuild(pid: str, meta: dict) -> dict | None:
    """Last resort when a snapshot is missing (e.g. it was written to an instance's local disk before a restart
    because no bucket was configured): re-solve from the saved levers - deterministic - and store it again."""
    scen = meta.get("scenario") or "baseline"
    if scen not in SCENARIOS:
        return None
    lv = {k: v for k, v in (meta.get("levers") or {}).items() if v is not None}
    shocks, base = lv.pop("shocks", None) or [], SCENARIOS[scen]["params"].get("shocks") or []
    if shocks[:len(base)] == base:  # the scenario adds its own shocks; pass only the extra ones
        shocks = shocks[len(base):]
    if shocks:
        lv["shocks"] = shocks
    try:
        res = service.run(scen, lv)
    except Exception as e:  # noqa: BLE001 - a policy that cannot be rebuilt is reported as missing
        print(f"[policies] could not rebuild {pid}: {type(e).__name__}: {e}")
        return None
    try:
        service.snapshot_put(f"policies/{pid}/run", res)
    except Exception as e:  # noqa: BLE001
        print(f"[policies] could not re-store the snapshot of {pid}: {e}")
    return res


def update(pid: str, name: str | None = None, note: str | None = None, deleted: bool | None = None) -> dict | None:
    with _lock:
        m = st.docs().get("policies", pid) if POLICY_ID.match(pid or "") else None
        if not m:
            return None
        if name is not None:
            m["name"] = clean(name, NAME_MAX) or m.get("name") or "Untitled policy"
        if note is not None:
            m["note"] = clean(note, NOTE_MAX)
        if deleted is not None:
            m["deleted"] = bool(deleted)
            m["deleted_at"] = st.now_iso() if deleted else None
        m["updated_at"] = st.now_iso()
        st.docs().put("policies", pid, m)
        _listing.bump()
    service.forget(f"p-{pid}")  # the cached copy carries the old name/deleted flag
    return _meta_public(m)


def purge(pid: str) -> bool:
    if not POLICY_ID.match(pid or ""):
        return False
    with _lock:
        if not st.docs().get("policies", pid):
            return False
        st.blobs().delete_prefix(f"policies/{pid}/")
        st.docs().delete("policies", pid)
        _listing.bump()
    service.forget(f"p-{pid}")
    return True


def cached_name(pid: str) -> str | None:
    """A policy's name from this instance's cached library listing, without a store read (None if not cached)."""
    for m in _listing.cached():
        if m.get("id") == pid:
            return m.get("name")
    return None


def find(ref: str) -> dict | None:
    """A policy by id ('p-xxxx' or 'xxxx') or by name (exact, then closest match), preferring saved over deleted."""
    ref = clean(ref, NAME_MAX)
    if not ref:
        return None
    pid = ref[2:] if ref.startswith("p-") else ref
    if POLICY_ID.match(pid):
        m = get_meta(pid)
        if m:
            return m
    rows = sorted(list_all(), key=lambda r: bool(r.get("deleted")))
    low = ref.lower()
    for r in rows:
        if (r.get("name") or "").lower() == low:
            return r
    try:
        from rapidfuzz import fuzz, process
        names = [r.get("name") or "" for r in rows]
        hit = process.extractOne(ref, names, scorer=fuzz.WRatio, score_cutoff=80)
        return rows[hit[2]] if hit else None
    except Exception:  # pragma: no cover
        hits = [r for r in rows if low in (r.get("name") or "").lower()]
        return hits[0] if hits else None
