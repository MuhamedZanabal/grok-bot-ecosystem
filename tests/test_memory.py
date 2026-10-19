"""cycle-006 memory substrate: provenance, wrapping, dedup, idempotency, persistence, and
the browser.fetch -> memory wiring (local fixture only)."""
import json
import sys
import unittest

from tests.egress_fixtures import Fixture
from tests.helpers import Harness
from tests.memory_helpers import MEM_KEY, attach_memory, search, write
from control_plane import Status
from memory import MemoryStore, NOTICE, fts5_available, ingest_fetch
from tools import ToolRegistry, register_browser_fetch


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False)  # strict control plane: memory binds as trusted_host
        self.mem = attach_memory(self.h)

    def tearDown(self):
        self.mem.close()
        self.h.close()

    def test_search_mode_recorded(self):
        print(f"\nMEMORY_SEARCH_MODE={self.mem.search_mode} fts5_available={fts5_available()}", file=sys.stderr)
        self.assertIn(self.mem.search_mode, ("fts5", "like"))

    def test_bind_is_audited_as_trusted_host(self):
        with open(self.h.audit_path) as fh:
            ev = [json.loads(line) for line in fh if '"handler_bound"' in line]
        kinds = {e["data"]["capability"]: e["data"]["isolation"] for e in ev}
        self.assertEqual(kinds["cap.memory.write"], "trusted_host")
        self.assertEqual(kinds["cap.memory.search"], "trusted_host")

    def test_write_then_search_returns_wrapped_untrusted_with_provenance(self):
        url = "http://127.0.0.1:8080/a/b?x=1&y=%20Z"
        out = write(self.h, "The quick brown fox jumps.", "op-mem-00001", url=url, source_ref="op-fetch-1")
        self.assertEqual(out.status, Status.EXECUTED, out)
        res = search(self.h, "brown fox", "op-mem-00002").result
        self.assertEqual(res["type"], "untrusted_data_batch")
        self.assertEqual(res["notice"], NOTICE)
        item = res["items"][0]
        self.assertEqual(item["type"], "untrusted_data")
        self.assertEqual(item["trust"], "untrusted")
        self.assertEqual(item["provenance"]["origin_url"], url)  # byte-exact
        self.assertEqual(item["provenance"]["source_id"], "test.src")
        self.assertEqual(item["provenance"]["source_ref"], "op-fetch-1")
        self.assertEqual(item["provenance"]["expires_at"] - item["provenance"]["created_at"], 30 * 86400)
        self.assertEqual(len(item["provenance"]["content_sha256"]), 64)

    def test_persists_and_verifies_across_restart(self):
        write(self.h, "persistent knowledge item", "op-mem-00003")
        again = MemoryStore(self.h.mem_path, MEM_KEY, clock=self.h.wall)
        rows, bad = again.search("persistent", 5)
        self.assertEqual((len(rows), bad), (1, []))
        self.assertEqual(again.verify_chain()[0], True)
        again.close()

    def test_chain_head_anchored_in_audit_log(self):
        write(self.h, "anchor me", "op-mem-00004")
        with open(self.h.audit_path) as fh:
            macs = [json.loads(line)["data"]["mac"] for line in fh if '"memory_write"' in line]
        self.assertEqual(macs[-1], self.mem.head())


class BrowserToMemoryTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(inprocess=False)
        self.mem = attach_memory(self.h, grant=("agent.alpha", "system.ingest"))
        self.fx = Fixture()
        reg = ToolRegistry(self.h.policy, self.h.cp, self.h.audit)
        register_browser_fetch(reg, (self.fx.entry,), by="zanabal")
        self.h.policy.grant("agent.alpha", "cap.browser.fetch")

    def tearDown(self):
        self.fx.close()
        self.mem.close()
        self.h.close()

    def test_fetch_is_ingested_as_untrusted_record_via_control_plane(self):
        url = self.fx.base + "/page?q=fox&n=2"
        args = {"url": url}
        op = "op-fetch-mem-01"
        tok = self.h.approve("cap.browser.fetch", op=op, args=args)
        fetched = self.h.cp.execute(self.h.req("cap.browser.fetch", op=op, args=args, token=tok))
        self.assertEqual(fetched.status, Status.EXECUTED, fetched)
        out = ingest_fetch(self.h.cp, "system.ingest", op, fetched.result)
        self.assertEqual(out.status, Status.EXECUTED, out)
        again = ingest_fetch(self.h.cp, "system.ingest", op, fetched.result)
        self.assertEqual(again.status, Status.REPLAYED)  # idempotent re-ingest
        item = search(self.h, "Visible text", "op-mem-search-1").result["items"][0]
        print(f"\nMEMORY_FROM_FETCH {json.dumps(item['provenance'], sort_keys=True)}", file=sys.stderr)
        self.assertEqual(item["provenance"]["origin_url"], url)
        self.assertEqual(item["provenance"]["source_id"], "cap.browser.fetch")
        self.assertEqual(item["provenance"]["source_ref"], op)
        self.assertEqual(item["trust"], "untrusted")
        self.assertIn("Fixture Page", item["content"])

    def test_failed_fetch_is_not_ingested(self):
        self.assertIsNone(ingest_fetch(self.h.cp, "system.ingest", "op-x-00000001", {"ok": False, "error": "blocked"}))


if __name__ == "__main__":
    unittest.main()
