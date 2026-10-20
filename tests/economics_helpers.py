"""Wiring for cycle-009 economics tests: a ledger attached to a Harness control plane."""
import os
import sqlite3

from tests.helpers import Harness  # noqa: I001 (puts src/ on sys.path first)
from economics import Caps, EconomicsLedger, Pricing
from orchestrator import MockProvider

ECON_KEY = b"e" * 32
SECRET = "ghp_" + "Z" * 30


def attach(h: Harness, caps: Caps = Caps(), per_call=None, token=None, expected=None, path=None):
    path = path or os.path.join(h.dir.name, "econ.sqlite")
    led = EconomicsLedger(path, ECON_KEY, h.audit, caps=caps, clock=h.wall,
                          pricing=Pricing(per_call or {}, token or {}, expected or {}))
    h.cp.economics = led
    h.econ_path = path
    return led


def sql(path, stmt, args=()):
    db = sqlite3.connect(path)
    db.execute(stmt, args)
    db.commit()
    db.close()


def rows(path, kind=None):
    db = sqlite3.connect(path)
    q = "SELECT seq, kind, subject FROM entries" + (" WHERE kind=?" if kind else "") + " ORDER BY seq"
    out = db.execute(q, (kind,) if kind else ()).fetchall()
    db.close()
    return out


class UsageProvider(MockProvider):
    """MockProvider that also reports provider usage fields (or None) per call."""

    def __init__(self, script, usage, name="mockusage"):
        super().__init__(script, name=name)
        self._usage = list(usage)
        self.last_usage = None

    def complete(self, request_json):
        self.last_usage = self._usage.pop(0) if self._usage else None
        return super().complete(request_json)
