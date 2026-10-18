"""Persistent storage: saved policies, co-pilot chat sessions and solved optimizer runs.

On Google Cloud (both inside the free tier, created by deploy/deploy.sh):
  * Firestore (native mode)  - small JSON documents: policy metadata and chat sessions
  * Cloud Storage bucket     - run snapshots: the public result (gzipped JSON) + the district x crop frame (Parquet)
Locally (no GOOGLE_CLOUD_PROJECT / STORE_BUCKET) both live in a folder on disk.

Everything degrades gracefully: if a Google service is unreachable or not set up, documents fall back to the bucket
and then to the local folder, so the app keeps working (only then are saved items lost when the instance restarts);
/api/health reports which backend is live.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from .config import settings

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STAMPS = "agrisus_meta"  # one version stamp per listed collection (see Listing)
COLLECTIONS = {"policies", "chats", STAMPS}


def _check_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key or "//" in key:
        raise ValueError("invalid storage key")
    return key


def _check_doc(coll: str, doc_id: str) -> None:
    if coll not in COLLECTIONS or not _ID.match(doc_id or ""):
        raise ValueError("invalid document id")


def _plain(doc: dict) -> dict:
    """JSON round trip: numpy scalars -> Python numbers, tuples -> lists (Firestore and json.dump accept the result)."""
    return json.loads(json.dumps(doc, default=lambda x: x.item() if hasattr(x, "item") else str(x)))


def _order(docs: list, order_by: str, limit: int, fields: list | None) -> list:
    docs.sort(key=lambda x: str(x.get(order_by, "")), reverse=True)
    docs = docs[:limit]
    return [{k: d.get(k) for k in fields} for d in docs] if fields else docs


# ------------------------------------------------------------------------------------------------- blobs
class LocalBlobs:
    kind, persistent, shared = "local disk", True, False

    def __init__(self, root: str) -> None:
        self.root = os.path.join(root, "blobs")
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, *_check_key(key).split("/"))

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes | None:
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        p = self._path(prefix.rstrip("/"))
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)


class GcsBlobs:
    kind, persistent, shared = "Cloud Storage", True, True

    def __init__(self, bucket: str) -> None:
        from google.cloud import storage
        self.bucket = storage.Client(project=settings.project).bucket(bucket)
        self.name = bucket
        probe = self.bucket.blob("_probe/ready")
        if not probe.exists(timeout=15):  # a missing bucket also reads as "no object": the write below raises then
            probe.upload_from_string(b"ok", content_type="text/plain", timeout=30)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.bucket.blob(_check_key(key)).upload_from_string(data, content_type=content_type, timeout=120)

    def get(self, key: str) -> bytes | None:
        from google.api_core.exceptions import NotFound
        try:
            return self.bucket.blob(_check_key(key)).download_as_bytes(timeout=120)
        except NotFound:
            return None

    def delete_prefix(self, prefix: str) -> None:
        for b in self.bucket.list_blobs(prefix=_check_key(prefix), timeout=60):
            b.delete(timeout=30)


# ------------------------------------------------------------------------------------------------- documents
class LocalDocs:
    kind, persistent, shared = "local disk", True, False

    def __init__(self, root: str) -> None:
        self.root = os.path.join(root, "docs")
        self._lock = threading.Lock()

    def _path(self, coll: str, doc_id: str) -> str:
        _check_doc(coll, doc_id)
        return os.path.join(self.root, coll, f"{doc_id}.json")

    def put(self, coll: str, doc_id: str, doc: dict) -> None:
        p = self._path(coll, doc_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with self._lock:
            tmp = f"{p}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_plain(doc), f, ensure_ascii=False)
            os.replace(tmp, p)

    def get(self, coll: str, doc_id: str) -> dict | None:
        try:
            with open(self._path(coll, doc_id), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def delete(self, coll: str, doc_id: str) -> None:
        try:
            os.remove(self._path(coll, doc_id))
        except FileNotFoundError:
            pass

    def list(self, coll: str, order_by: str = "updated_at", limit: int = 100, fields: list | None = None) -> list:
        if coll not in COLLECTIONS:
            raise ValueError("invalid collection")
        d = os.path.join(self.root, coll)
        out = []
        for name in os.listdir(d) if os.path.isdir(d) else []:
            if name.endswith(".json") and _ID.match(name[:-5]):
                doc = self.get(coll, name[:-5])
                if doc:
                    out.append(doc)
        return _order(out, order_by, limit, fields)


class GcsDocs:
    """JSON documents as objects in the bucket: the fallback when Firestore is not available in the project."""
    kind, persistent, shared = "Cloud Storage", True, True

    def __init__(self, blobs: GcsBlobs) -> None:
        self.blobs = blobs

    def put(self, coll: str, doc_id: str, doc: dict) -> None:
        _check_doc(coll, doc_id)
        self.blobs.put(f"docs/{coll}/{doc_id}.json", json.dumps(_plain(doc), ensure_ascii=False).encode(),
                       "application/json")

    def get(self, coll: str, doc_id: str) -> dict | None:
        _check_doc(coll, doc_id)
        raw = self.blobs.get(f"docs/{coll}/{doc_id}.json")
        return json.loads(raw) if raw else None

    def delete(self, coll: str, doc_id: str) -> None:
        _check_doc(coll, doc_id)
        from google.api_core.exceptions import NotFound
        try:
            self.blobs.bucket.blob(f"docs/{coll}/{doc_id}.json").delete(timeout=30)
        except NotFound:
            pass

    def list(self, coll: str, order_by: str = "updated_at", limit: int = 100, fields: list | None = None) -> list:
        if coll not in COLLECTIONS:
            raise ValueError("invalid collection")
        from concurrent.futures import ThreadPoolExecutor

        def read(b):
            try:
                return json.loads(b.download_as_bytes(timeout=30))
            except Exception:  # pragma: no cover - deleted between list and read
                return None
        found = list(self.blobs.bucket.list_blobs(prefix=f"docs/{coll}/", timeout=60))
        with ThreadPoolExecutor(max_workers=16) as ex:
            out = [d for d in ex.map(read, found) if d]
        return _order(out, order_by, limit, fields)


class FirestoreDocs:
    kind, persistent, shared = "Firestore", True, True

    def __init__(self) -> None:
        from google.cloud import firestore
        self._fs = firestore
        self.db = firestore.Client(project=settings.project, database=settings.firestore_database)
        self.db.collection("policies").document("_probe").get(timeout=15)  # raises if the database is missing

    def put(self, coll: str, doc_id: str, doc: dict) -> None:
        _check_doc(coll, doc_id)
        self.db.collection(coll).document(doc_id).set(_plain(doc), timeout=30)

    def get(self, coll: str, doc_id: str) -> dict | None:
        _check_doc(coll, doc_id)
        snap = self.db.collection(coll).document(doc_id).get(timeout=30)
        return snap.to_dict() if snap.exists else None

    def delete(self, coll: str, doc_id: str) -> None:
        _check_doc(coll, doc_id)
        self.db.collection(coll).document(doc_id).delete(timeout=30)

    def list(self, coll: str, order_by: str = "updated_at", limit: int = 100, fields: list | None = None) -> list:
        if coll not in COLLECTIONS:
            raise ValueError("invalid collection")
        q = self.db.collection(coll)
        if fields:  # projection: listing chats must not download every message of every session
            q = q.select(list(fields))
        q = q.order_by(order_by, direction=self._fs.Query.DESCENDING).limit(limit)
        return [s.to_dict() for s in q.stream(timeout=30)]


# ------------------------------------------------------------------------------------------------- selection
_lock = threading.Lock()
_state: dict = {}


def _local_dir() -> str:
    d = settings.store_dir
    os.makedirs(d, exist_ok=True)
    return d


def _init() -> dict:
    notes = []
    blobs = docs = None
    if settings.project and settings.store_bucket:
        try:
            blobs = GcsBlobs(settings.store_bucket)
        except Exception as e:
            notes.append(f"Cloud Storage bucket unavailable ({type(e).__name__})")
            print(f"[store] bucket {settings.store_bucket} unavailable: {e}")
    elif settings.project:
        notes.append("STORE_BUCKET not set")
    mode = settings.doc_store
    if mode in ("auto", "firestore") and settings.project:
        try:
            docs = FirestoreDocs()
        except Exception as e:
            notes.append(f"Firestore unavailable ({type(e).__name__})")
            print(f"[store] Firestore unavailable: {e}")
    if docs is None and mode in ("auto", "firestore", "gcs") and blobs is not None:
        docs = GcsDocs(blobs)
    if blobs is None:
        blobs = LocalBlobs(_local_dir())
    if docs is None:
        docs = LocalDocs(_local_dir())
    st = {"blobs": blobs, "docs": docs, "notes": notes}
    print(f"[store] documents: {docs.kind}; run snapshots: {blobs.kind}" + (f" ({'; '.join(notes)})" if notes else ""))
    return st


def _get() -> dict:
    with _lock:
        if not _state:
            _state.update(_init())
        return _state


def blobs():
    return _get()["blobs"]


def docs():
    return _get()["docs"]


class Listing:
    """A collection listing cached per instance and revalidated with a single read of a version stamp that every
    write bumps. Opening the policy library then costs one Firestore read instead of one per policy, and a policy
    saved on another Cloud Run instance still shows up at once. Callers must treat the returned rows as read-only."""

    def __init__(self, coll: str, **list_kw) -> None:
        self.coll, self.kw = coll, list_kw
        self._lock = threading.Lock()
        self._stamp: str | None = None
        self._rows: list = []

    def rows(self) -> list:
        stamp = str((docs().get(STAMPS, self.coll) or {}).get("v") or "-")
        with self._lock:
            if stamp == self._stamp:
                return list(self._rows)
        rows = docs().list(self.coll, **self.kw)  # documents first, stamp after: a racing write re-lists next time
        with self._lock:
            self._stamp, self._rows = stamp, rows
        return list(rows)

    def bump(self) -> None:
        """Call after every write to the collection (after the write itself)."""
        with self._lock:
            self._stamp = None
        try:
            docs().put(STAMPS, self.coll, {"id": self.coll, "v": new_id(12), "updated_at": now_iso()})
        except Exception as e:  # pragma: no cover - the write succeeded; other instances catch up on the next bump
            print(f"[store] could not bump the {self.coll} listing stamp: {type(e).__name__}: {e}")


def status() -> dict:
    s = _get()
    shared = s["docs"].shared and s["blobs"].shared
    return {"documents": s["docs"].kind, "snapshots": s["blobs"].kind, "shared": shared,
            # local disk on Cloud Run is the container's memory: gone on restart, not seen by other instances
            "durable": shared or not os.getenv("K_SERVICE"),
            **({"notes": s["notes"]} if s["notes"] else {})}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(n: int = 10) -> str:
    import secrets
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))
