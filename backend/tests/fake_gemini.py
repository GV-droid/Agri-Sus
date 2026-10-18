"""A scriptable fake Gemini API for offline tests: generateContent and streamGenerateContent (alt=sse).

    fake = FakeGemini(lambda call: [("sleep", 1), ("chunk", [{"text": "Hello"}]), ("chunk", [{"text": "!"}], "STOP")])
    fake.install()          # every app.gemini call now goes to the fake

script(call) returns the steps for one request; call = {"n", "model", "stream", "body"}. Steps:
    ("sleep", s)                      wait (before the first chunk = slow first token; between = a stall)
    ("chunk", [parts], finish=None)   one streamed chunk (non-streamed calls merge all chunks into one response)
    ("error", code, status)           an HTTP error (only before the first chunk)
    ("drop",)                         close the connection mid-stream
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def text_of(body: dict) -> str:
    return " ".join(p.get("text", "") for c in body.get("contents") or [] for p in c.get("parts") or [])


def has_function_response(body: dict) -> bool:
    last = (body.get("contents") or [{}])[-1]
    return any("functionResponse" in p or "function_response" in p for p in last.get("parts") or [])


def no_tools(body: dict) -> bool:
    """True when the request forbids tool calls (the co-pilot's wrap-up call)."""
    tc = body.get("toolConfig") or body.get("tool_config") or {}
    mode = (tc.get("functionCallingConfig") or tc.get("function_calling_config") or {}).get("mode", "")
    return str(mode).upper() == "NONE"


class FakeGemini:
    def __init__(self, script):
        self.script = script
        self.calls: list = []
        self._lock = threading.Lock()
        self.url = ""

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                path = self.path.split("?")[0]
                model = path.rsplit("/", 1)[-1].split(":")[0]
                streaming = ":streamGenerateContent" in path
                with fake._lock:
                    call = {"n": len(fake.calls) + 1, "model": model, "stream": streaming, "body": body,
                            "t": time.monotonic()}
                    fake.calls.append(call)
                try:
                    steps = fake.script(call)
                except Exception as e:  # a broken script is a test bug: make it visible
                    steps = [("error", 500, f"script error {e!r}")]
                try:
                    (self._stream if streaming else self._unary)(steps)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _error(self, code, status):
                b = json.dumps({"error": {"code": code, "message": f"fake {status}", "status": status}}).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            @staticmethod
            def _chunk(parts, finish=None):
                cand = {"content": {"role": "model", "parts": parts}, "index": 0}
                if finish:
                    cand["finishReason"] = finish
                return {"candidates": [cand],
                        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}}

            def _unary(self, steps):
                parts, finish = [], "STOP"
                for st in steps:
                    if st[0] == "sleep":
                        time.sleep(st[1])
                    elif st[0] == "error":
                        return self._error(st[1], st[2])
                    elif st[0] == "drop":
                        self.close_connection = True
                        return
                    elif st[0] == "chunk":
                        parts += st[1]
                        finish = st[2] if len(st) > 2 and st[2] else finish
                b = json.dumps(self._chunk(parts, finish)).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _stream(self, steps):
                started = False
                for st in steps:
                    if st[0] == "sleep":
                        time.sleep(st[1])
                    elif st[0] == "error" and not started:
                        return self._error(st[1], st[2])
                    elif st[0] == "drop":
                        self.close_connection = True
                        if started:  # cut the chunked body without its terminating chunk
                            self.wfile.flush()
                        return
                    elif st[0] == "chunk":
                        if not started:
                            self.send_response(200)
                            self.send_header("content-type", "text/event-stream")
                            self.send_header("transfer-encoding", "chunked")
                            self.end_headers()
                            started = True
                        data = f"data: {json.dumps(self._chunk(st[1], st[2] if len(st) > 2 else None))}\r\n\r\n".encode()
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                if not started:
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        srv.daemon_threads = True
        srv.handle_error = lambda *a: None  # clients dropping kept-alive connections is expected here
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    def install(self):
        """Route app.gemini to this fake (call before the first Gemini call)."""
        from google import genai
        from google.genai import types

        from app import gemini
        if not self.url:
            self.start()
        client = genai.Client(api_key="fake-key", http_options=types.HttpOptions(base_url=self.url))
        gemini.client = lambda: client
        return self


def fake_env() -> None:
    """Environment for an app that talks to a fake Gemini (set before importing app modules)."""
    os.environ.update(GEMINI_API_KEY="fake-key", GOOGLE_GENAI_USE_VERTEXAI="false", DATA_BACKEND="local",
                      WRITE_TRACES_TO_BQ="false")
    os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
