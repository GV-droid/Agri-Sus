"""Background jobs for long AI work (co-pilot answers, policy documents).

The work runs on the server, independent of the HTTP connection that started it: a dropped connection, a sleeping
laptop or a proxy that gives up on a quiet stream no longer throws the work away. Each job keeps an append-only event
log; a client streams it (Server-Sent Events) and, after a dropped connection, resumes from the last event it saw
(GET /api/jobs/{id}/events?after=N). Finished jobs stay in memory for JOB_TTL_S so a late reconnect still gets the
whole answer, and the results themselves are persisted (chat sessions, policy documents), so even a different
instance can serve them.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import string
import threading
import time
from typing import Callable

JOB_ID = re.compile(r"^[a-z0-9]{16}$")
JOB_TTL_S = 30 * 60
MAX_JOBS = 400  # running + recently finished, per instance
MAX_EVENTS = 4000  # per job: a runaway producer cannot grow memory without bound
LIMITS = {"chat": 40, "document": 8}  # running jobs per instance, by kind
PER_OWNER = {"chat": 2, "document": 2}  # running jobs per visitor, by kind
_ALWAYS = {"done", "error", "session", "document", "job"}  # kept even when a job hits MAX_EVENTS


class Busy(RuntimeError):
    """Too many jobs of this kind are running (instance-wide or for this visitor)."""


def _new_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


def valid_id(job_id) -> bool:
    return isinstance(job_id, str) and bool(JOB_ID.match(job_id))


class Job:
    def __init__(self, kind: str, key: str | None = None, owner: str | None = None):
        self.id = _new_id()
        self.kind, self.key, self.owner = kind, key, owner
        self.created = time.time()
        self.finished_at: float | None = None
        self.status = "running"  # running | done | error | cancelled
        self.result = None
        self._events: list = []
        self._cond = threading.Condition()
        self._subs: list = []  # (event loop, asyncio.Event) of the streams following this job
        self._cancel = threading.Event()
        self._callbacks: list = []

    # ---- producer side (the job's thread)
    def emit(self, ev: dict) -> dict | None:
        with self._cond:
            if self.finished_at is not None:
                return None
            if len(self._events) >= MAX_EVENTS and ev.get("type") not in _ALWAYS:
                return None
            ev = {**ev, "seq": len(self._events) + 1}
            self._events.append(ev)
            self._cond.notify_all()
        self._wake()
        return ev

    def finish(self, status: str = "done", result=None) -> None:
        with self._cond:
            if self.finished_at is not None:
                return
            if not self._events or self._events[-1].get("type") != "done":  # every stream ends with 'done'
                self._events.append({"type": "done", "seq": len(self._events) + 1})
            self.status, self.result, self.finished_at = status, result, time.time()
            self._cond.notify_all()
            callbacks, self._callbacks = self._callbacks, []
        self._wake()
        for cb in callbacks:
            _safe(cb, self)

    def on_finish(self, cb: Callable[["Job"], None]) -> None:
        """Run cb(job) when the job finishes (at once if it already has)."""
        with self._cond:
            if self.finished_at is None:
                self._callbacks.append(cb)
                return
        _safe(cb, self)

    # ---- consumer side
    @property
    def done(self) -> bool:
        return self.finished_at is not None

    @property
    def last_seq(self) -> int:
        return len(self._events)

    def events_after(self, after: int) -> list:
        with self._cond:
            return self._events[max(0, after):]

    def wait(self, after: int, timeout: float) -> list:
        """Block (a worker thread) until there are events after `after`, the job finishes, or `timeout` passes."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while len(self._events) <= after and self.finished_at is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self._events[max(0, after):]

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def info(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "events": self.last_seq,
                "created": self.created, "finished": self.finished_at}

    # ---- asyncio streams (one per connected client)
    def _subscribe(self) -> asyncio.Event:
        e = asyncio.Event()
        with self._cond:
            self._subs.append((asyncio.get_running_loop(), e))
        return e

    def _unsubscribe(self, e: asyncio.Event) -> None:
        with self._cond:
            self._subs = [s for s in self._subs if s[1] is not e]

    def _wake(self) -> None:
        with self._cond:
            subs = list(self._subs)
        for loop, e in subs:
            try:
                loop.call_soon_threadsafe(e.set)
            except RuntimeError:  # that client's event loop is closed (server shutting down)
                pass


def _safe(cb, job) -> None:
    try:
        cb(job)
    except Exception as e:  # pragma: no cover - a callback must never break the job runner
        print(f"[jobs] {job.kind} {job.id} callback failed: {type(e).__name__}: {e}")


_jobs: dict = {}
_lock = threading.Lock()


def _gc_locked(now: float) -> None:
    for jid in [j.id for j in _jobs.values() if j.finished_at and j.finished_at < now - JOB_TTL_S]:
        del _jobs[jid]
    if len(_jobs) >= MAX_JOBS:
        for j in sorted((j for j in _jobs.values() if j.finished_at), key=lambda j: j.finished_at)[:len(_jobs) - MAX_JOBS + 1]:
            del _jobs[j.id]


def _running(kind: str) -> list:
    return [j for j in _jobs.values() if j.kind == kind and j.finished_at is None]


def get(job_id: str) -> Job | None:
    if not valid_id(job_id):
        return None
    with _lock:
        return _jobs.get(job_id)


def find(key: str) -> Job | None:
    """The running job with this dedupe key, if any."""
    with _lock:
        return next((j for j in _jobs.values() if j.key == key and j.finished_at is None), None)


def start(kind: str, fn: Callable[[Job], object], key: str | None = None, owner: str | None = None) -> tuple:
    """Run fn(job) in a background thread -> (job, created). With a key, a running job with the same key is
    returned instead of starting a second one (created=False). fn emits events with job.emit(...) and returns the
    job's result; the stream always ends with a 'done' event. Raises Busy when too many jobs are running."""
    with _lock:
        now = time.time()
        _gc_locked(now)
        if key:
            same = next((j for j in _jobs.values() if j.key == key and j.finished_at is None), None)
            if same is not None:
                return same, False
        running = _running(kind)
        if len(running) >= LIMITS.get(kind, 20):
            raise Busy("The AI features are very busy right now - please try again in a minute.")
        if owner and sum(1 for j in running if j.owner == owner) >= PER_OWNER.get(kind, 2):
            raise Busy("You already have answers in progress - wait for them to finish (or stop one) first."
                       if kind == "chat" else "You already have documents being written - wait for them to finish.")
        job = Job(kind, key, owner)
        job.emit({"type": "job", "id": job.id, "kind": kind})  # first event: what to resume if the stream drops
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job, fn), daemon=True, name=f"job-{kind}-{job.id[:6]}").start()
    return job, True


def _run(job: Job, fn) -> None:
    try:
        result = fn(job)
        job.finish("cancelled" if job.cancelled else "done", result)
    except Exception as e:
        print(f"[jobs] {job.kind} {job.id} failed: {type(e).__name__}: {e}")
        job.emit({"type": "error", "text": f"This request failed ({type(e).__name__}); please try again."})
        job.finish("error")


def stats() -> dict:
    with _lock:
        return {k: len(_running(k)) for k in LIMITS} | {"kept": len(_jobs)}


async def sse(job: Job, after: int = 0, heartbeat: float = 10.0, prefix: list | None = None):
    """Server-Sent Events for one client: the job's events after `after` (each carries its `seq`; resume with
    ?after=<last seq seen>), live, with a comment line every `heartbeat` seconds so proxies never see a silent stream.
    A client that disconnects only stops its own stream; the job keeps running."""
    sub = job._subscribe()
    try:
        yield ": stream open\n\n"
        for ev in prefix or ():
            yield f"data: {json.dumps(ev, default=str)}\n\n"
        i = max(0, int(after))
        while True:
            batch = job.events_after(i)
            for ev in batch:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            i += len(batch)
            if job.done and i >= job.last_seq:
                return
            sub.clear()
            if job.last_seq > i or job.done:  # an event arrived between reading and clearing
                continue
            try:
                await asyncio.wait_for(sub.wait(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        job._unsubscribe(sub)
