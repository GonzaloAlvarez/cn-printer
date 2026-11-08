"""Unit tests for cn-printer's paperless-forwarder against fake_paperless (in-process)."""
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import forwarder as fw  # noqa: E402
import fake_paperless  # noqa: E402


class ForwarderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "out")
        self.srv = fake_paperless.serve(0, self.out, token="t0k", fail_names=["bad.pdf"], bind="127.0.0.1")
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.cfg = dict(fw.CFG)
        self.cfg.update({
            "PAPERLESS_URL": f"http://127.0.0.1:{self.port}", "PAPERLESS_TOKEN": "t0k",
            "PAPERLESS_TAGS": ["scanned"],
            "INBOX_DIR": os.path.join(self.tmp, "inbox"), "PROCESSED_DIR": os.path.join(self.tmp, "processed"),
            "FAILED_DIR": os.path.join(self.tmp, "failed"), "TEXTFILE_DIR": os.path.join(self.tmp, "textfile"),
            "STATE_FILE": os.path.join(self.tmp, "state.json"), "STABILITY_SECONDS": 0.2,
            "PROCESSED_RETENTION_DAYS": 30,
        })
        for d in ("INBOX_DIR", "PROCESSED_DIR", "FAILED_DIR"):
            os.makedirs(self.cfg[d])
        self.api = fw.Paperless(self.cfg["PAPERLESS_URL"], "t0k")
        self.f = fw.Forwarder(self.cfg, self.api)

    def tearDown(self):
        self.stop_server()

    def stop_server(self):
        # shutdown() blocks forever if serve_forever() already returned — guard it.
        if getattr(self, "_server_up", True):
            self.srv.shutdown()
            self.srv.server_close()
            self._server_up = False

    def drop(self, name, data=b"%PDF-1.4 test"):
        p = os.path.join(self.cfg["INBOX_DIR"], name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def run_until_settled(self, rounds=10):
        for _ in range(rounds):
            pending = self.f.once()
            if pending == 0:
                return 0
            time.sleep(0.3)
        return pending

    def test_success_path(self):
        self.drop("scan_1.pdf")
        self.assertEqual(self.run_until_settled(), 0)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["PROCESSED_DIR"], "scan_1.pdf")))
        self.assertTrue(os.path.exists(os.path.join(self.out, "scan_1.pdf")))
        self.assertEqual(os.listdir(self.cfg["INBOX_DIR"]), [])
        self.assertEqual(self.f.state["forwarded_total"], 1)
        task = next(iter(self.srv.state.tasks.values()))
        self.assertEqual(task["fields"]["title"], ["scan_1"])
        self.assertEqual(task["fields"]["tags"], ["1"])          # 'scanned' created on demand → id 1
        self.assertEqual(self.srv.state.tags, {"scanned": 1})

    def test_failure_path(self):
        self.drop("bad.pdf")
        self.assertEqual(self.run_until_settled(), 0)
        failed = os.path.join(self.cfg["FAILED_DIR"], "bad.pdf")
        self.assertTrue(os.path.exists(failed))
        with open(failed + ".paperless-FAILED.txt") as fh:
            self.assertIn("FAIL_NAMES", fh.read())
        self.assertEqual(self.f.state["failed_total"], 1)

    def test_unreachable_keeps_file(self):
        self.stop_server()
        self.drop("waiting.pdf")
        pending = self.f.once()
        self.assertEqual(pending, 1)
        self.assertFalse(self.f.reachable)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["INBOX_DIR"], "waiting.pdf")))
        text = fw.render_metrics(pending, self.f.state, self.f.reachable)
        self.assertIn("scan_forwarder_pending_files 1", text)
        self.assertIn("scan_forwarder_paperless_reachable 0", text)

    def test_bad_token_leaves_file(self):
        self.f.api.token = "wrong"
        self.drop("auth.pdf")
        self.assertEqual(self.f.once(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["INBOX_DIR"], "auth.pdf")))

    def test_sidecar_prevents_reupload(self):
        p = self.drop("once.pdf")
        self.assertEqual(self.run_until_settled(), 0)
        self.assertEqual(len(self.srv.state.tasks), 1)
        # a stale .task sidecar for a vanished task → failed/, no new upload
        q = self.drop("ghost.pdf")
        with open(q + ".task", "w") as fh:
            fh.write("00000000-0000-0000-0000-000000000000")
        self.assertEqual(self.run_until_settled(), 0)
        self.assertEqual(len(self.srv.state.tasks), 1)
        self.assertTrue(os.path.exists(os.path.join(self.cfg["FAILED_DIR"], "ghost.pdf")))
        self.assertFalse(os.path.exists(p))

    def test_skips_markers_and_unstable(self):
        self.drop("note.paperless-FAILED.txt", b"x")
        self.drop("partial.pdf")
        self.assertEqual(len(fw.candidates(self.cfg["INBOX_DIR"])), 1)
        self.assertFalse(self.f.stable(os.path.join(self.cfg["INBOX_DIR"], "partial.pdf")))  # first sight

    def test_janitor_and_metrics_file(self):
        old = os.path.join(self.cfg["PROCESSED_DIR"], "old.pdf")
        with open(old, "wb") as fh:
            fh.write(b"x")
        os.utime(old, (time.time() - 40 * 86400,) * 2)
        self.assertEqual(fw.janitor(self.cfg["PROCESSED_DIR"], 30), 1)
        fw.write_metrics(self.cfg["TEXTFILE_DIR"], fw.render_metrics(0, self.f.state, True))
        with open(os.path.join(self.cfg["TEXTFILE_DIR"], "scan_forwarder.prom")) as fh:
            self.assertIn("scan_forwarder_paperless_reachable 1", fh.read())

    def test_multipart_shape(self):
        body = fw.encode_multipart("B", [("title", "t"), ("tags", "1")], "document", "a.pdf", b"DATA")
        self.assertTrue(body.startswith(b"--B\r\n"))
        self.assertIn(b'name="document"; filename="a.pdf"', body)
        self.assertTrue(body.endswith(b"\r\n--B--\r\n"))


if __name__ == "__main__":
    unittest.main()
