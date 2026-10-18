"""Co-pilot chat sessions: every conversation is saved (Firestore when available) and shared by everyone using this
deployment - there is no login, so the UI says so. Sessions can be reopened, continued and deleted."""
from __future__ import annotations

import re
import secrets
import threading

from . import store as st

CHAT_ID = re.compile(r"^[a-z0-9]{12}$")
MAX_CHATS = 200  # oldest sessions are dropped beyond this
MAX_MESSAGES = 60  # per session (Firestore documents are capped at 1 MiB)
MAX_CONTENT = 6000
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]+")
_lock = threading.Lock()
LIST_FIELDS = ["id", "title", "created_at", "updated_at", "n_messages", "language"]
LIST_MAX = 50
_listing = st.Listing("chats", order_by="updated_at", limit=LIST_MAX, fields=LIST_FIELDS)


def valid_id(cid) -> bool:
    return isinstance(cid, str) and bool(CHAT_ID.match(cid))


def new_id() -> str:
    return st.new_id(12)


def _title(q: str) -> str:
    t = re.sub(r"\s+", " ", _CTRL.sub(" ", q or "")).strip()
    return (t[:67] + "...") if len(t) > 70 else (t or "New chat")


def _text(s, limit: int = MAX_CONTENT) -> str:
    return _CTRL.sub(" ", str(s or ""))[:limit]


def list_all(limit: int = LIST_MAX) -> list:
    return [{k: r.get(k) for k in LIST_FIELDS} for r in _listing.rows()[:limit]]


def get(cid: str) -> dict | None:
    if not valid_id(cid):
        return None
    return st.docs().get("chats", cid)


def delete(cid: str) -> bool:
    if not valid_id(cid):
        return False
    with _lock:
        if not st.docs().get("chats", cid):
            return False
        st.docs().delete("chats", cid)
        _listing.bump()
    return True


def _prune() -> None:
    rows = st.docs().list("chats", order_by="updated_at", limit=MAX_CHATS + 25, fields=["id", "updated_at"])
    old = [r["id"] for r in rows[MAX_CHATS:] if valid_id(r.get("id"))]
    for cid in old:
        st.docs().delete("chats", cid)
    if old:
        _listing.bump()


def record(cid: str, question: str, answer: dict, language: str = "en") -> dict:
    """Append one exchange (the user's question + the co-pilot's answer) to a session, creating it if needed.
    answer: {content, citations?, policies?, simulations?, model?, error?}"""
    if not valid_id(cid):
        raise ValueError("invalid chat id")
    now = st.now_iso()
    a = {"role": "assistant", "content": _text(answer.get("content")), "at": now}
    for k in ("citations", "policies"):
        if answer.get(k):
            a[k] = [{kk: _text(v, 500) for kk, v in x.items() if kk in ("title", "uri", "id", "name")}
                    for x in answer[k][:12] if isinstance(x, dict)]
    if answer.get("simulations"):
        a["simulations"] = [str(x)[:16] for x in answer["simulations"][-6:]]
    for k in ("model", "error"):
        if answer.get(k):
            a[k] = _text(answer[k], 300)
    with _lock:
        doc = st.docs().get("chats", cid)
        created = doc is None
        if created:
            doc = {"id": cid, "title": _title(question), "created_at": now, "messages": []}
        msgs = list(doc.get("messages") or [])
        msgs += [{"role": "user", "content": _text(question), "at": now}, a]
        doc["messages"] = msgs[-MAX_MESSAGES:]
        doc.update(updated_at=now, n_messages=len(doc["messages"]), language=language)
        st.docs().put("chats", cid, doc)
        _listing.bump()
    if created and secrets.randbelow(8) == 0:  # housekeeping on ~1 in 8 new sessions: a prune lists every session
        try:
            _prune()
        except Exception as e:  # pragma: no cover - pruning is housekeeping only
            print(f"[chats] prune skipped: {type(e).__name__}: {e}")
    return {"id": cid, "title": doc["title"], "created": created}
