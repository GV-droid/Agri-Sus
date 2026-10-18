"""Co-pilot and policy-document resilience: a slow, stalled, rate-limited or failing Gemini never surfaces as a
timeout. Runs offline (about a minute) against a scriptable fake Gemini and a real uvicorn server, so streaming,
dropped connections and resuming are exercised end to end:
    .venv/bin/python -W ignore backend/tests/test_agent_resilience.py        (or: pytest backend/tests)
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from fake_gemini import FakeGemini, fake_env, has_function_response, no_tools  # noqa: E402

fake_env()
# a throw-away store: the chats, runs and documents these tests write never reach the developer's own store
os.environ["AGRISUS_STORE_DIR"] = tempfile.mkdtemp(prefix="agrisus-test-")
atexit.register(shutil.rmtree, os.environ["AGRISUS_STORE_DIR"], True)
# short stall limits so the tests run in seconds (production: 60 s to the first token, 45 s of silence)
os.environ.update(GEMINI_FIRST_TOKEN_S="1.5", GEMINI_IDLE_S="1.5", GEMINI_RETRIES="2")
os.environ.pop("API_DOCS", None)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import agent, documents, gemini, jobs, main, service  # noqa: E402
from app.config import settings  # noqa: E402

_script: dict = {"fn": lambda call: [("chunk", [{"text": "ok"}], "STOP")]}
FAKE = FakeGemini(lambda call: _script["fn"](call)).install()
SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")  # a tool or parameter name leaking into the activity feed
ANSWER = ("Bathinda should move about a fifth of its paddy area to cotton and kharif maize: both earn more per drop "
          "of water, and the district's aquifer is falling by almost a metre a year. ")
OTHER = ("Ludhiana has the stronger case for maize, because its procurement network and dryers are already in "
         "place; Bathinda's best switch is cotton on its lighter soils. ")


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _serve() -> str:
    port = _port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


BASE = _serve()


# ---- helpers
def fcall(name: str, args: dict) -> dict:
    return {"functionCall": {"name": name, "args": args}}


def say(text: str, size: int = 60, gap: float = 0.0) -> list:
    """An answer streamed in small chunks."""
    steps = []
    for i in range(0, len(text), size):
        if gap:
            steps.append(("sleep", gap))
        steps.append(("chunk", [{"text": text[i:i + size]}]))
    steps.append(("chunk", [{"text": ""}], "STOP"))
    return steps


def reset() -> int:
    """Before each test: forget remembered fallbacks and visitor limits -> the number of fake calls so far."""
    gemini._working_model.clear()
    gemini._no_thinking.clear()
    main._hits.clear()
    return len(FAKE.calls)


def _read(resp, until=None) -> list:
    evs = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            evs.append(json.loads(line[6:]))
            if until is not None and until(evs[-1]):
                break
    return evs


def ask(question: str, until=None, session_id: str | None = None) -> list:
    body = {"messages": [{"role": "user", "content": question}], "language": "en"}
    if session_id:
        body["session_id"] = session_id
    with httpx.Client(base_url=BASE, timeout=60) as c, c.stream("POST", "/api/agent/chat", json=body) as r:
        assert r.status_code == 200, r.read()
        return _read(r, until)


def answer_text(evs: list) -> str:
    out: list = []
    for e in evs:
        if e["type"] == "text":
            out.append(e["text"])
        elif e["type"] == "text_reset":
            out = []
    return "".join(out)


def of(evs: list, kind: str, **match) -> list:
    return [e for e in evs if e["type"] == kind and all(e.get(k) == v for k, v in match.items())]


def assert_well_formed(evs: list) -> None:
    assert evs[0]["type"] == "job" and jobs.valid_id(evs[0]["id"]), evs[:2]
    assert evs[-1]["type"] == "done" and [e["type"] for e in evs].count("done") == 1, evs[-3:]
    seqs = [e["seq"] for e in evs if "seq" in e]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), seqs  # no gaps, no duplicates
    assert of(evs, "session", saved=True), "the exchange was not saved"
    for e in evs:  # the activity feed is plain language: no tool names, parameters or JSON
        for k in ("label", "text") if e["type"] in ("tool_call", "status", "progress") else ():
            assert e.get(k) is None or not SNAKE.search(e[k]), e
        assert "args" not in e, e


# ---- tests
def test_job_registry():
    j, created = jobs.start("unit", lambda job: (job.emit({"type": "x"}), time.sleep(0.3), "r")[-1], key="k1",
                            owner="o")
    j2, created2 = jobs.start("unit", lambda job: None, key="k1", owner="o")
    assert created and not created2 and j2 is j  # same key while running: the same job
    assert jobs.find("k1") is j
    evs = j.wait(after=0, timeout=2)
    assert evs[0]["type"] == "job" and evs[0]["seq"] == 1
    while not j.done:
        time.sleep(0.05)
    assert j.result == "r" and j.status == "done" and j.events_after(0)[-1]["type"] == "done"
    assert [e["seq"] for e in j.events_after(1)] == list(range(2, j.last_seq + 1))
    seen = []
    j.on_finish(lambda job: seen.append(job.id))  # after the end: runs at once
    assert seen == [j.id] and jobs.find("k1") is None
    blockers = [jobs.start("chat", lambda job: time.sleep(0.6), owner="busy-owner")[0] for _ in range(2)]
    try:
        jobs.start("chat", lambda job: None, owner="busy-owner")
        raise AssertionError("a third running answer for one visitor was accepted")
    except jobs.Busy:
        pass
    bad = jobs.start("unit", lambda job: 1 / 0)[0]
    while not bad.done or any(not b.done for b in blockers):
        time.sleep(0.05)
    assert bad.status == "error" and [e["type"] for e in bad.events_after(0)][-2:] == ["error", "done"]
    assert not jobs.valid_id("../etc") and not jobs.valid_id("A" * 16) and jobs.valid_id(j.id)


def test_slow_first_token_is_retried_on_a_faster_model():
    n0 = reset()

    def script(call):
        k = call["n"] - n0
        if k == 1:  # the primary model never starts answering
            return [("sleep", 4), ("chunk", [{"text": "too late"}], "STOP")]
        if not has_function_response(call["body"]):
            return [("chunk", [fcall("get_district_profile", {"district": "Bathinda"})], "STOP")]
        return say(ANSWER)
    _script["fn"] = script
    t0 = time.monotonic()
    evs = ask("What should Bathinda grow after paddy?")
    assert_well_formed(evs)
    assert time.monotonic() - t0 < 15
    calls = FAKE.calls[n0:]
    assert calls[0]["model"] == settings.agent_model and calls[1]["model"] != calls[0]["model"], \
        [c["model"] for c in calls]
    def budget(call):
        tc = (call["body"].get("generationConfig") or {}).get("thinkingConfig") or {}
        return tc.get("thinkingBudget", tc.get("thinking_budget"))
    assert budget(calls[1]) == settings.fast_thinking_budget, budget(calls[1])  # the retry thinks less
    assert calls[2]["model"] == calls[1]["model"], "the rest of the answer stays on the model that responded"
    assert len(of(evs, "model")) == 2
    assert of(evs, "status", phase="retry"), [e for e in evs if e["type"] == "status"]
    tc = of(evs, "tool_call")
    assert len(tc) == 1 and "Bathinda" in tc[0]["label"] and tc[0]["id"] == "t1", tc
    assert of(evs, "tool_result", id="t1", ok=True)
    assert answer_text(evs) == ANSWER and not of(evs, "error")


def test_stalled_answer_is_discarded_and_retried():
    n0 = reset()

    def script(call):
        k = call["n"] - n0
        if k == 1:
            return [("chunk", [fcall("get_district_profile", {"district": "Bathinda"})], "STOP")]
        if k == 2:  # starts answering, then goes silent mid-sentence
            return [("chunk", [{"text": OTHER * 2}]), ("sleep", 5), ("chunk", [{"text": "never"}], "STOP")]
        return say(ANSWER)
    _script["fn"] = script
    evs = ask("Explain Bathinda's best crop switch")
    assert_well_formed(evs)
    kinds = [e["type"] for e in evs]
    assert "text_reset" in kinds and kinds.index("text_reset") > kinds.index("text"), kinds
    assert answer_text(evs) == ANSWER
    sid = of(evs, "session")[0]["id"]
    saved = httpx.get(f"{BASE}/api/chats/{sid}").json()
    last = [m for m in saved["messages"] if m["role"] == "assistant"][-1]
    assert last["content"] == ANSWER.strip(), last["content"][:200]  # the discarded half-answer is not saved
    assert last["turn"] == evs[0]["id"] and last.get("elapsed_s", 0) > 0
    steps = last["activity"]
    assert any(s.get("ok") is True and "Bathinda" in s["text"] for s in steps), steps
    assert any(s.get("kind") == "retry" for s in steps), steps


def test_soft_deadline_wraps_up_without_tools():
    n0 = reset()
    old = settings.agent_deadline_s
    settings.agent_deadline_s = 2.0

    def script(call):
        if no_tools(call["body"]):
            return say("Wrapped up from the district profiles gathered so far. " * 4)
        k = call["n"] - n0
        return [("sleep", 0.8), ("chunk", [fcall("get_district_profile", {"district": ["Bathinda", "Ludhiana",
                                                                                        "Sangrur", "Patiala"][k % 4]})],
                                   "STOP")]
    _script["fn"] = script
    try:
        evs = ask("Compare every Punjab district in detail")
    finally:
        settings.agent_deadline_s = old
    assert_well_formed(evs)
    assert of(evs, "status", phase="wrapup")
    assert no_tools(FAKE.calls[-1]["body"])  # the last call could not call tools
    assert answer_text(evs).startswith("Wrapped up") and len(of(evs, "tool_result")) >= 1


def test_gemini_outage_after_some_steps_summarises_them():
    n0 = reset()

    def script(call):
        if call["n"] - n0 == 1:
            return [("chunk", [fcall("get_district_profile", {"district": "Bathinda"})], "STOP")]
        return [("error", 503, "UNAVAILABLE")]
    _script["fn"] = script
    evs = ask("What should Bathinda grow?")
    assert_well_formed(evs)
    assert of(evs, "status", phase="fallback") and not of(evs, "error")
    text = answer_text(evs)
    assert "here is what I found" in text and "Bathinda" in text, text
    assert len(FAKE.calls) - n0 == 1 + 1 + settings.gemini_retries  # the first call, then 1 + N retries


def test_total_outage_falls_back_to_the_offline_analyst():
    reset()
    _script["fn"] = lambda call: [("error", 503, "UNAVAILABLE")]
    evs = ask("Which districts save the most groundwater?")
    assert_well_formed(evs)
    err = of(evs, "error")
    assert err and "did not respond" in err[0]["text"], err
    assert any(e.get("backend") == agent.OFFLINE_FALLBACK for e in of(evs, "model"))
    assert answer_text(evs).strip()


def test_stop_cancels_the_answer():
    n0 = reset()
    _script["fn"] = lambda call: [("chunk", [{"text": "**Comparing water use**", "thought": True}])] + say(
        ANSWER * 4, size=20, gap=0.25)
    with httpx.Client(base_url=BASE, timeout=60) as c, \
            c.stream("POST", "/api/agent/chat", json={"messages": [{"role": "user", "content": "Bathinda water?"}]}) as r:
        jid = None
        evs = []
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            evs.append(json.loads(line[6:]))
            if evs[-1]["type"] == "job":
                jid = evs[-1]["id"]
            if evs[-1]["type"] == "text" and jid:
                t0 = time.monotonic()
                res = httpx.post(f"{BASE}/api/jobs/{jid}/cancel")
                assert res.status_code == 200 and res.json()["id"] == jid, res.text
                jid = None
    assert time.monotonic() - t0 < 3, "Stop took too long"
    assert_well_formed(evs)
    assert of(evs, "status", phase="stopped") and answer_text(evs).endswith("_Stopped._")
    assert of(evs, "status", phase="thinking", text="Comparing water use")
    assert jobs.get(evs[0]["id"]).status == "cancelled" and len(FAKE.calls) - n0 == 1


def test_dropped_connection_resumes_from_the_last_event():
    n0 = reset()

    def script(call):
        if call["n"] - n0 == 1:
            return [("chunk", [fcall("get_district_profile", {"district": "Bathinda"})], "STOP")]
        return say(ANSWER, size=25, gap=0.3)
    _script["fn"] = script
    first = ask("What should Bathinda grow?", until=lambda e: e["type"] == "tool_result")  # then the tab "closes"
    jid, last = first[0]["id"], first[-1]["seq"]
    time.sleep(1.0)
    job = jobs.get(jid)
    assert job is not None and not job.done, "the answer must keep running without a client"
    with httpx.Client(base_url=BASE, timeout=60) as c, c.stream("GET", f"/api/jobs/{jid}/events?after={last}") as r:
        assert r.status_code == 200
        rest = _read(r)
    assert rest[0]["seq"] == last + 1 and rest[-1]["type"] == "done"
    assert_well_formed(first + rest)
    assert answer_text(first + rest) == ANSWER
    assert httpx.get(f"{BASE}/api/jobs/not-a-job/events").status_code == 400
    assert httpx.get(f"{BASE}/api/jobs/{'a' * 16}/events").status_code == 404
    assert httpx.post(f"{BASE}/api/jobs/{'a' * 16}/cancel").status_code == 404


def test_parallel_steps_and_a_plain_language_feed():
    n0 = reset()
    seen: dict = {}

    def script(call):
        if call["n"] - n0 == 1:
            return [("chunk", [{"text": "**Checking `get_district_profile` for both**", "thought": True}]),
                    ("chunk", [{"text": "I'll compare the two districts first."},
                               fcall("get_district_profile", {"district": "Bathinda"}),
                               fcall("get_district_profile", {"district": "Ludhiana"})], "STOP")]
        seen["order"] = [p["functionResponse"]["name"] for p in call["body"]["contents"][-1]["parts"]]
        seen["districts"] = [json.dumps(p["functionResponse"]["response"])[:4000]
                             for p in call["body"]["contents"][-1]["parts"]]
        return say(OTHER)
    _script["fn"] = script
    evs = ask("Compare Bathinda and Ludhiana")
    assert_well_formed(evs)
    calls = of(evs, "tool_call")
    assert [c["id"] for c in calls] == ["t1", "t2"] and "Bathinda" in calls[0]["label"] and "Ludhiana" in calls[1]["label"]
    assert {r["id"] for r in of(evs, "tool_result")} == {"t1", "t2"}
    assert of(evs, "status", phase="plan", text="I'll compare the two districts first.")
    assert of(evs, "status", phase="thinking", text="Checking the district profile for both")
    assert answer_text(evs) == OTHER  # the preamble is a plan line, not part of the answer
    assert seen["order"] == ["get_district_profile"] * 2
    assert "Bathinda" in seen["districts"][0] and "Ludhiana" in seen["districts"][1]  # results in call order


def test_scope_guard():
    n0 = reset()
    evs = ask("Write a python function that reverses a linked list")
    assert_well_formed(evs)
    assert of(evs, "status", phase="scope") and "outside what I can help with" in answer_text(evs)
    assert len(FAKE.calls) == n0, "an unrelated question must not cost a model call"
    for q in ("Who won the IPL in 2024?", "Give me a cake recipe", "best bollywood movie this year"):
        assert agent.off_topic(q), q
    for q in ("What is the MSP of wheat this year?", "How does El Nino affect the monsoon?",
              "python script to forecast wheat yield", "Is PM-KISAN useful for tenant farmers?",
              "Which crops does this plan move?", "Explain section E of the document", "बाजरा कहाँ उगाएँ?"):
        assert not agent.off_topic(q), q
    _script["fn"] = lambda call: say("Hello! Ask me about any district's crop plan. ")
    evs = ask("hi")
    assert_well_formed(evs)


def test_document_jobs_are_shared_and_errors_reported():
    from test_security import _restore_store, _temp_store
    reset()
    rid = "0123456789ab"
    fake_res = {"run_id": rid, "params": {}, "kpis": [{}]}
    real_get, real_gen = service.get, documents.generate_events
    started = threading.Event()

    def slow(res, scope, language, regenerate, instructions, refresh_research, source):
        started.set()
        yield {"type": "step", "id": "draft", "status": "running", "label": "Drafting the document"}
        time.sleep(1.5)
        yield {"type": "document", "document": {"run_id": res["run_id"], "scope": scope, "language": language,
                                                "version": 1, "title": "Plan brief", "policy_name": None}}
        yield {"type": "done"}

    store_, saved, tmp = _temp_store()
    service.get = lambda r: fake_res if r == rid else real_get(r)
    documents.generate_events = slow
    out: dict = {}

    def write(name: str, body: dict):
        with httpx.Client(base_url=BASE, timeout=60) as c, c.stream("POST", "/api/documents", json=body) as r:
            out[name] = (r.status_code, _read(r))
    try:
        a = threading.Thread(target=write, args=("a", {"run_id": rid}))
        a.start()
        assert started.wait(5)
        info = httpx.get(f"{BASE}/api/documents/{rid}").json()
        assert info["busy"] and info["job"] and jobs.valid_id(info["job"]["id"])
        write("b", {"run_id": rid, "regenerate": True, "instructions": "Shorter section E"})  # while it is written
        a.join(10)
        (sa, ea), (sb, eb) = out["a"], out["b"]
        assert sa == sb == 200
        assert ea[0]["type"] == "job" and eb[1]["type"] == "job" and ea[0]["id"] == eb[1]["id"] == info["job"]["id"]
        assert eb[0]["type"] == "notice" and "not applied" in eb[0]["text"]
        assert of(ea, "document") and of(eb, "document") and ea[-1]["type"] == eb[-1]["type"] == "done"

        def broken(*a, **k):
            raise ValueError("this plan has no districts")
            yield  # pragma: no cover
        documents.generate_events = broken
        with httpx.Client(base_url=BASE, timeout=60) as c, \
                c.stream("POST", "/api/documents", json={"run_id": rid, "regenerate": True}) as r:
            evs = _read(r)
        err = of(evs, "error")
        assert err and err[0]["code"] == 400 and "no districts" in err[0]["message"] and evs[-1]["type"] == "done", evs
    finally:
        service.get, documents.generate_events = real_get, real_gen
        _restore_store(store_, saved, tmp)


def test_document_writer_keeps_a_mostly_written_draft():
    n0 = reset()
    body = "".join(f"## {sid}. {title}\n\n" + ("Evidence and clauses for this section. " * 18) + "\n\n"
                   for sid, title in documents.SECTIONS[1:6])  # sections A-E, then the writer stalls
    _script["fn"] = lambda call: [("chunk", [{"text": "## Executive summary\n\nThe plan in brief.\n\n"}])] + \
        say(body, size=400, gap=0.25)[:-1] + [("sleep", 5), ("chunk", [{"text": "never"}], "STOP")]
    old = documents.WRITER_FIRST_S, documents.WRITER_IDLE_S
    documents.WRITER_FIRST_S, documents.WRITER_IDLE_S = 3.0, 1.5
    meta = {"title": "Plan brief", "version": 1, "date": "1 January 2026", "scope": "India", "language": "en",
            "history": []}
    try:
        got = list(documents.draft_stream({"plan": {}}, {"groups": []}, {}, None, meta, deadline_s=60))
    finally:
        documents.WRITER_FIRST_S, documents.WRITER_IDLE_S = old
    kind, (md, model, note) = got[-1]
    assert kind == "result" and "part-way" in note, note
    assert "## E." in md and "never" not in md
    assert any(k == "progress" and v.startswith("section") for k, v in got[:-1]), got[:-1]
    assert len(FAKE.calls) - n0 == 1, "a mostly written draft is kept, not rewritten from scratch"


if __name__ == "__main__":
    failed = 0
    for name, fn in [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]:
        t = time.monotonic()
        try:
            fn()
            print(f"PASS {name} ({time.monotonic() - t:.1f} s)")
        except Exception as e:  # pragma: no cover
            import traceback
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
