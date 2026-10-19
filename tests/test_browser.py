"""cap.browser.fetch end to end: registry -> policy -> approval -> sandboxed worker -> egress
proxy -> local fixture server (no live external sites)."""
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from tests.egress_fixtures import Fixture
from tests.helpers import Harness
from control_plane import Status
from tools import SubprocessHandler, ToolRegistry, browser_fetch_spec, register_browser_fetch


class BrowserFetchTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False)
        self.fx = Fixture()
        self.reg = ToolRegistry(self.h.policy, self.h.cp, self.h.audit)
        register_browser_fetch(self.reg, (self.fx.entry,), by="zanabal")
        self.h.policy.grant("agent.alpha", "cap.browser.fetch")

    def tearDown(self):
        self.fx.close()
        self.h.close()

    def _call(self, url, op, approve=True):
        args = {"url": url}
        tok = self.h.approve("cap.browser.fetch", op=op, args=args) if approve else None
        return self.h.cp.execute(self.h.req("cap.browser.fetch", op=op, args=args, token=tok))

    def test_fetch_extracts_title_text_links_through_proxy(self):
        out = self._call(self.fx.base + "/page", "op-browse-0001")
        self.assertEqual(out.status, Status.EXECUTED, out)
        r = out.result
        print(f"\nBROWSER_FETCH {json.dumps({k: r[k] for k in ('ok', 'status', 'title', 'links')})}", file=sys.stderr)
        self.assertTrue(r["ok"])
        self.assertEqual(r["title"], "Fixture Page")
        self.assertIn("Visible text & more.", r["text"])
        self.assertNotIn("steal()", r["text"])  # script content is not page text
        self.assertEqual(r["links"], [self.fx.base + "/next", "http://other.invalid/z"])
        self.assertEqual(self.fx.hits, ["/page"])
        with open(self.h.audit_path) as fh:
            ev = [json.loads(line) for line in fh]
        allowed = [e for e in ev if e["event"] == "egress_allowed"]
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["data"]["capability"], "cap.browser.fetch")

    def test_browser_is_approval_gated(self):
        out = self._call(self.fx.base + "/page", "op-browse-0002", approve=False)
        self.assertEqual(out.status, Status.APPROVAL_REQUIRED)
        self.assertEqual(self.fx.hits, [])

    def test_schema_rejects_non_http_url_before_execution(self):
        out = self._call("file:///etc/passwd", "op-browse-0003")
        self.assertEqual(out.status, Status.REJECTED)

    def test_ungranted_actor_denied(self):
        args = {"url": self.fx.base + "/page"}
        tok = self.h.approve("cap.browser.fetch", op="op-browse-0004", args=args, actor="agent.beta")
        out = self.h.cp.execute(self.h.req("cap.browser.fetch", op="op-browse-0004", args=args,
                                           actor="agent.beta", token=tok))
        self.assertEqual(out.status, Status.DENIED)
        self.assertEqual(self.fx.hits, [])


class TlsTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False)

    def tearDown(self):
        self.h.close()

    def test_worker_has_system_ca_roots_when_egress_enabled(self):
        from tools import BUILTIN_ROOT, WorkerSpec
        spec = WorkerSpec(BUILTIN_ROOT, "browser", "tls_roots", egress=("example.com:443",))
        r = SubprocessHandler(spec, audit=self.h.audit, capability="c")({})
        self.assertGreater(r["x509_ca"], 0)

    def test_tls_certificate_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            key, crt = os.path.join(d, "k.pem"), os.path.join(d, "c.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                            "-days", "1", "-subj", "/CN=127.0.0.1"], check=True, capture_output=True)
            fx = Fixture()
            fx.srv.shutdown()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, key)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), fx.srv.RequestHandlerClass)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            port = srv.server_address[1]
            try:
                r = SubprocessHandler(browser_fetch_spec((f"127.0.0.1:{port}",)), audit=self.h.audit,
                                      capability="cap.browser.fetch")({"url": f"https://127.0.0.1:{port}/page"})
            finally:
                srv.shutdown()
                srv.server_close()
                fx.srv.server_close()
        self.assertEqual(r["error"], "tls_verify_failed")  # self-signed: refused, never silently accepted


if __name__ == "__main__":
    unittest.main()
