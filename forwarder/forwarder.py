#!/usr/bin/env python3
"""cn-printer paperless-forwarder — /srv/scans/inbox → Paperless-ngx.

Loop (every POLL_SECONDS):
  * every regular file in INBOX_DIR whose size+mtime have been stable for
    STABILITY_SECONDS is uploaded with POST /api/documents/post_document/
    (multipart: document, title = name without extension, tags = PAPERLESS_TAGS
    resolved to ids, created on demand); the returned task id is written to
    <name>.task next to the file so a restart never re-uploads;
  * every file with a .task sidecar is polled at /api/tasks/?task_id= until the
    task is SUCCESS (→ PROCESSED_DIR) or FAILURE (→ FAILED_DIR plus
    <name>.paperless-FAILED.txt with Paperless's reason);
  * files older than PROCESSED_RETENTION_DAYS are deleted from PROCESSED_DIR;
  * scan_forwarder_*.prom is rewritten atomically in TEXTFILE_DIR for
    node-exporter (kaiser alerts ScanInboxStuck / ScanForwardFailed).
Paperless unreachable → nothing moves, exponential backoff capped at 5 min,
the loop keeps going. Moves are renames (same filesystem, owner preserved).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CFG = {
    "PAPERLESS_URL": os.environ.get("PAPERLESS_URL", "").rstrip("/"),
    "PAPERLESS_TOKEN": os.environ.get("PAPERLESS_TOKEN", ""),
    "PAPERLESS_TAGS": [t.strip() for t in os.environ.get("PAPERLESS_TAGS", "scanned").split(",") if t.strip()],
    "INBOX_DIR": os.environ.get("INBOX_DIR", "/scans/inbox"),
    "PROCESSED_DIR": os.environ.get("PROCESSED_DIR", "/scans/processed"),
    "FAILED_DIR": os.environ.get("FAILED_DIR", "/scans/failed"),
    "TEXTFILE_DIR": os.environ.get("TEXTFILE_DIR", ""),
    "STATE_FILE": os.environ.get("STATE_FILE", "/scans/.forwarder-state.json"),
    "POLL_SECONDS": float(os.environ.get("POLL_SECONDS", "5")),
    "STABILITY_SECONDS": float(os.environ.get("STABILITY_SECONDS", "3")),
    "TASK_POLL_SECONDS": float(os.environ.get("TASK_POLL_SECONDS", "3")),
    "PROCESSED_RETENTION_DAYS": float(os.environ.get("PROCESSED_RETENTION_DAYS", "30")),
    "SCANS_UID": int(os.environ.get("SCANS_UID", "1000")),
    "SCANS_GID": int(os.environ.get("SCANS_GID", "1000")),
    "CA_BUNDLE": os.environ.get("CA_BUNDLE", ""),
    "HTTP_TIMEOUT": float(os.environ.get("HTTP_TIMEOUT", "120")),
}
SKIP_SUFFIXES = (".task", ".paperless-FAILED.txt", ".gdrive-FAILED.txt", ".part", ".tmp")


def log(msg: str) -> None:
    print(f"[forwarder] {msg}", flush=True)


# ── HTTP ──────────────────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status, self.body = status, body


class Paperless:
    def __init__(self, url: str, token: str, ca_bundle: str = "", timeout: float = 120):
        self.url, self.token, self.timeout = url, token, timeout
        if ca_bundle and not os.path.exists(ca_bundle):
            log(f"CA_BUNDLE {ca_bundle} not found — falling back to the system trust store")
            ca_bundle = ""
        self.ctx = ssl.create_default_context(cafile=ca_bundle or None)
        self.tag_cache: dict[str, int] = {}

    def _request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None):
        headers = {"Accept": "application/json; version=10", "Authorization": f"Token {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")) from None

    def ping(self) -> bool:
        try:
            self._request("GET", "/api/tags/?page_size=1")
            return True
        except ApiError as e:
            log(f"paperless answered {e} — check PAPERLESS_TOKEN / PAPERLESS_URL")
            return False
        except Exception as e:  # noqa: BLE001 — network errors of every flavour
            log(f"paperless unreachable: {e.__class__.__name__}: {e}")
            return False

    def tag_id(self, name: str) -> int:
        if name in self.tag_cache:
            return self.tag_cache[name]
        res = self._request("GET", f"/api/tags/?name__iexact={urllib.parse.quote(name)}")
        results = res.get("results", []) if isinstance(res, dict) else []
        if results:
            tid = int(results[0]["id"])
        else:
            created = self._request("POST", "/api/tags/", json.dumps({"name": name, "matching_algorithm": 0}).encode(),
                                    "application/json")
            tid = int(created["id"])
            log(f"created tag {name!r} (id={tid})")
        self.tag_cache[name] = tid
        return tid

    def upload(self, path: str, title: str, tag_ids: list[int]) -> str:
        boundary = "----cnprinter" + uuid.uuid4().hex
        fields = [("title", title)] + [("tags", str(t)) for t in tag_ids]
        with open(path, "rb") as f:
            data = f.read()
        body = encode_multipart(boundary, fields, "document", os.path.basename(path), data)
        res = self._request("POST", "/api/documents/post_document/", body, f"multipart/form-data; boundary={boundary}")
        task = res if isinstance(res, str) else (res or {}).get("task_id") or str(res)
        return task.strip().strip('"')

    def task(self, task_id: str) -> dict | None:
        res = self._request("GET", f"/api/tasks/?task_id={urllib.parse.quote(task_id)}")
        items = res if isinstance(res, list) else (res or {}).get("results", [])
        return items[0] if items else None


def encode_multipart(boundary: str, fields: list[tuple[str, str]], file_field: str, filename: str, data: bytes) -> bytes:
    out = bytearray()
    for name, value in fields:
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    ctype = "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    out += data
    out += f"\r\n--{boundary}--\r\n".encode()
    return bytes(out)


# ── files ─────────────────────────────────────────────────────────────────────
def candidates(inbox: str) -> list[str]:
    try:
        names = sorted(os.listdir(inbox))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        p = os.path.join(inbox, n)
        if n.startswith(".") or n.endswith(SKIP_SUFFIXES) or not os.path.isfile(p):
            continue
        out.append(p)
    return out


def chown_quiet(path: str, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid)
    except (PermissionError, OSError):
        pass


def move(src: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.exists(dest):
        stem, ext = os.path.splitext(os.path.basename(src))
        dest = os.path.join(dest_dir, f"{stem}.{int(time.time())}{ext}")
    os.replace(src, dest)
    return dest


def write_marker(path: str, text: str, uid: int, gid: int) -> None:
    with open(path, "w") as f:
        f.write(text)
    chown_quiet(path, uid, gid)


def janitor(processed: str, days: float) -> int:
    if days <= 0 or not os.path.isdir(processed):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for name in os.listdir(processed):
        p = os.path.join(processed, name)
        try:
            if os.path.isfile(p) and os.stat(p).st_mtime < cutoff:
                os.remove(p)
                n += 1
        except OSError:
            pass
    return n


# ── state + metrics ───────────────────────────────────────────────────────────
def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"forwarded_total": 0, "failed_total": 0, "last_success": 0}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def render_metrics(pending: int, state: dict, reachable: bool) -> str:
    return (
        "# HELP scan_forwarder_pending_files Files waiting in the scan inbox (not yet confirmed by Paperless).\n"
        "# TYPE scan_forwarder_pending_files gauge\n"
        f"scan_forwarder_pending_files {pending}\n"
        "# HELP scan_forwarder_forwarded_total Scans Paperless consumed successfully.\n"
        "# TYPE scan_forwarder_forwarded_total counter\n"
        f"scan_forwarder_forwarded_total {state['forwarded_total']}\n"
        "# HELP scan_forwarder_failed_total Scans Paperless rejected (moved to failed/).\n"
        "# TYPE scan_forwarder_failed_total counter\n"
        f"scan_forwarder_failed_total {state['failed_total']}\n"
        "# HELP scan_forwarder_last_success_timestamp_seconds Unix time of the last successful consumption.\n"
        "# TYPE scan_forwarder_last_success_timestamp_seconds gauge\n"
        f"scan_forwarder_last_success_timestamp_seconds {state['last_success']}\n"
        "# HELP scan_forwarder_paperless_reachable 1 when the Paperless API answered the last probe.\n"
        "# TYPE scan_forwarder_paperless_reachable gauge\n"
        f"scan_forwarder_paperless_reachable {1 if reachable else 0}\n"
    )


def write_metrics(textfile_dir: str, text: str) -> None:
    if not textfile_dir:
        return
    try:
        os.makedirs(textfile_dir, exist_ok=True)
        tmp = os.path.join(textfile_dir, ".scan_forwarder.prom.tmp")
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, os.path.join(textfile_dir, "scan_forwarder.prom"))
    except OSError as e:
        log(f"cannot write metrics to {textfile_dir}: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────
class Forwarder:
    def __init__(self, cfg: dict, api: Paperless):
        self.cfg, self.api = cfg, api
        self.seen: dict[str, tuple[int, float, float]] = {}   # path → (size, mtime, first_seen_stable)
        self.state = load_state(cfg["STATE_FILE"])
        self.backoff = 0
        self.reachable = False

    def stable(self, path: str) -> bool:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self.seen.pop(path, None)
            return False
        now = time.time()
        prev = self.seen.get(path)
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime:
            return now - prev[2] >= self.cfg["STABILITY_SECONDS"]
        self.seen[path] = (st.st_size, st.st_mtime, now)
        return False

    def settle(self, path: str, task_id: str) -> bool:
        """Poll one task; move the file when it finished. Returns True when settled."""
        info = self.api.task(task_id)
        # Paperless 3 reports Celery states in lower case ("success"); older
        # releases and the test stand-in use upper case — compare normalised.
        status = str((info or {}).get("status", "")).upper()
        if status == "SUCCESS":
            dest = move(path, self.cfg["PROCESSED_DIR"])
            self._rm(path + ".task")
            self.state["forwarded_total"] += 1
            self.state["last_success"] = int(time.time())
            m = re.search(r"document id (\d+)", str(info.get("result", "")))
            doc = info.get("related_document") or (m.group(1) if m else "?")
            log(f"consumed {os.path.basename(path)} (document {doc}) → {dest}")
            return True
        if status == "FAILURE" or (info is None):
            reason = (info or {}).get("result") or "task vanished from Paperless before it finished"
            dest = move(path, self.cfg["FAILED_DIR"])
            self._rm(path + ".task")
            write_marker(dest + ".paperless-FAILED.txt",
                         f"Paperless did not consume {os.path.basename(path)} (task {task_id}):\n{reason}\n",
                         self.cfg["SCANS_UID"], self.cfg["SCANS_GID"])
            self.state["failed_total"] += 1
            log(f"FAILED {os.path.basename(path)}: {str(reason)[:200]} → {dest}")
            return True
        return False

    @staticmethod
    def _rm(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def submit(self, path: str) -> None:
        title = os.path.splitext(os.path.basename(path))[0]
        tag_ids = [self.api.tag_id(t) for t in self.cfg["PAPERLESS_TAGS"]]
        task_id = self.api.upload(path, title, tag_ids)
        with open(path + ".task", "w") as f:
            f.write(task_id)
        chown_quiet(path + ".task", self.cfg["SCANS_UID"], self.cfg["SCANS_GID"])
        log(f"uploaded {os.path.basename(path)} → task {task_id}")

    def once(self) -> int:
        """One pass. Returns the number of files still pending."""
        inbox = self.cfg["INBOX_DIR"]
        files = candidates(inbox)
        pending = len(files)
        if not files:
            self.reachable = self.api.ping() if not self.reachable else True
            return 0
        if not self.api.ping():
            self.reachable = False
            return pending
        self.reachable = True
        for path in files:
            try:
                task_file = path + ".task"
                if os.path.exists(task_file):
                    with open(task_file) as f:
                        task_id = f.read().strip()
                    if task_id and self.settle(path, task_id):
                        pending -= 1
                        continue
                    if not task_id:
                        self._rm(task_file)
                    continue
                if not self.stable(path):
                    continue
                self.submit(path)
            except ApiError as e:
                if e.status in (401, 403):
                    log(f"auth error from Paperless ({e}); leaving {os.path.basename(path)} in the inbox")
                else:
                    log(f"error on {os.path.basename(path)}: {e}; will retry")
            except Exception as e:  # noqa: BLE001
                log(f"error on {os.path.basename(path)}: {e.__class__.__name__}: {e}; will retry")
        return pending

    def run(self) -> None:
        cfg = self.cfg
        for d in (cfg["INBOX_DIR"], cfg["PROCESSED_DIR"], cfg["FAILED_DIR"]):
            os.makedirs(d, exist_ok=True)
        log(f"ready: inbox={cfg['INBOX_DIR']} → {cfg['PAPERLESS_URL']} tags={cfg['PAPERLESS_TAGS']} "
            f"poll={cfg['POLL_SECONDS']}s retention={cfg['PROCESSED_RETENTION_DAYS']}d")
        while True:
            pending = self.once()
            removed = janitor(cfg["PROCESSED_DIR"], cfg["PROCESSED_RETENTION_DAYS"])
            if removed:
                log(f"janitor removed {removed} processed scan(s) older than {cfg['PROCESSED_RETENTION_DAYS']:g} days")
            save_state(cfg["STATE_FILE"], self.state)
            write_metrics(cfg["TEXTFILE_DIR"], render_metrics(pending, self.state, self.reachable))
            if pending and not self.reachable:
                self.backoff = min(300, (self.backoff * 2) if self.backoff else cfg["POLL_SECONDS"])
                log(f"{pending} file(s) waiting; retrying in {self.backoff:.0f}s")
                time.sleep(self.backoff)
            else:
                self.backoff = 0
                time.sleep(cfg["TASK_POLL_SECONDS"] if pending else cfg["POLL_SECONDS"])


def main() -> int:
    if not CFG["PAPERLESS_URL"] or not CFG["PAPERLESS_TOKEN"]:
        log("PAPERLESS_URL / PAPERLESS_TOKEN not set — idling (scans stay in the inbox)")
        while True:
            write_metrics(CFG["TEXTFILE_DIR"], render_metrics(len(candidates(CFG["INBOX_DIR"])), load_state(CFG["STATE_FILE"]), False))
            time.sleep(60)
    api = Paperless(CFG["PAPERLESS_URL"], CFG["PAPERLESS_TOKEN"], CFG["CA_BUNDLE"], CFG["HTTP_TIMEOUT"])
    Forwarder(CFG, api).run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
