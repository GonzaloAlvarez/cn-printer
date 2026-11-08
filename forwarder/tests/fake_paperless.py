#!/usr/bin/env python3
"""Stand-in for the Paperless-ngx REST API used by cn-printer's ./test and the
forwarder unit tests (stdlib only). Implements just what the forwarder calls:
  POST /api/documents/post_document/  multipart → saves the upload under OUT_DIR,
                                       returns a JSON task-id string (HTTP 200)
  GET  /api/tasks/?task_id=<id>        → [ {task_id, status, result, related_document} ]
                                       (status in lower case, as Paperless 3 reports it)
  GET  /api/tags/?name__iexact=<n>     → {results:[{id,name}]} (ids assigned on first sight)
  GET  /api/tags/?page_size=1          → ping
  POST /api/tags/                      → {id,name}
Env: OUT_DIR (default /out), PORT (8000), FAKE_TOKEN (require this token when set),
FAIL_NAMES (comma list of upload file names whose task ends in FAILURE).
Importable: serve(port, out_dir, token, fail_names) returns the server for tests.
"""
import json
import os
import re
import threading
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class State:
    def __init__(self, out_dir, token, fail_names):
        self.out_dir, self.token, self.fail_names = out_dir, token, set(fail_names)
        self.tasks, self.tags, self.docs = {}, {}, 0
        self.lock = threading.Lock()


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter
            print("[fake-paperless]", fmt % args, flush=True)

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            if not state.token:
                return True
            if self.headers.get("Authorization") == f"Token {state.token}":
                return True
            self._json(401, {"detail": "Authentication credentials were not provided."})
            return False

        def do_GET(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/tasks/":
                tid = (q.get("task_id") or [""])[0]
                t = state.tasks.get(tid)
                return self._json(200, [t] if t else [])
            if u.path == "/api/tags/":
                name = (q.get("name__iexact") or [""])[0]
                if name:
                    with state.lock:
                        tid = state.tags.get(name.lower())
                    return self._json(200, {"results": [{"id": tid, "name": name}] if tid else []})
                return self._json(200, {"results": [{"id": i, "name": n} for n, i in state.tags.items()][:1]})
            if u.path == "/api/":
                return self._json(200, {"documents": "/api/documents/"})
            self._json(404, {"detail": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if u.path == "/api/tags/":
                name = json.loads(raw or b"{}").get("name", "")
                with state.lock:
                    tid = state.tags.setdefault(name.lower(), len(state.tags) + 1)
                return self._json(201, {"id": tid, "name": name})
            if u.path == "/api/documents/post_document/":
                ctype = self.headers.get("Content-Type", "")
                msg = BytesParser(policy=email_policy).parsebytes(
                    b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
                filename, data, fields = None, b"", {}
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name == "document":
                        filename = part.get_filename() or "upload.bin"
                        data = part.get_payload(decode=True)
                    else:
                        fields.setdefault(name, []).append(part.get_content().strip())
                if not filename:
                    return self._json(400, {"document": ["No file was submitted."]})
                os.makedirs(state.out_dir, exist_ok=True)
                safe = re.sub(r"[^\w.\- ]", "_", filename)
                with open(os.path.join(state.out_dir, safe), "wb") as f:
                    f.write(data)
                tid = str(uuid.uuid4())
                with state.lock:
                    if filename in state.fail_names:
                        task = {"task_id": tid, "status": "failure", "related_document": None,
                                "result": f"Not consuming {filename}: rejected by FAIL_NAMES (test)"}
                    else:
                        state.docs += 1
                        task = {"task_id": tid, "status": "success", "related_document": str(state.docs),
                                "result": f"Success. New document id {state.docs} created"}
                    task["fields"] = fields
                    state.tasks[tid] = task
                return self._json(200, tid)
            self._json(404, {"detail": "not found"})

    return H


def serve(port=8000, out_dir="/out", token="", fail_names=(), bind="0.0.0.0"):
    state = State(out_dir, token, fail_names)
    srv = ThreadingHTTPServer((bind, port), make_handler(state))
    srv.state = state
    return srv


if __name__ == "__main__":
    s = serve(int(os.environ.get("PORT", "8000")), os.environ.get("OUT_DIR", "/out"),
              os.environ.get("FAKE_TOKEN", ""), [n for n in os.environ.get("FAIL_NAMES", "").split(",") if n])
    print(f"[fake-paperless] listening on {s.server_address} out={s.state.out_dir} fail={sorted(s.state.fail_names)}", flush=True)
    s.serve_forever()
