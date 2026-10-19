"""Operator CLI: kill switch and audit verification.

  python -m control_plane kill {engage,release,status} PATH [--reason TEXT]
  python -m control_plane audit-verify PATH
"""

import argparse
import sys

from .audit import verify_chain
from .killswitch import KillSwitch


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="control_plane")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kill")
    k.add_argument("action", choices=["engage", "release", "status"])
    k.add_argument("path")
    k.add_argument("--reason", default="operator")
    a = sub.add_parser("audit-verify")
    a.add_argument("path")
    ns = p.parse_args(argv)
    if ns.cmd == "kill":
        ks = KillSwitch(ns.path)
        if ns.action == "engage":
            ks.engage(ns.reason)
        elif ns.action == "release":
            ks.release()
        print("engaged" if ks.engaged() else "released")
        return 0
    ok, n, err = verify_chain(ns.path)
    print(f"ok records={n}" if ok else f"FAIL records={n} error={err}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
