"""CI step (D-0022): run a real research pass on a local fixture, then the audit-anchor
cross-check via the CLI (`python -m integrity`), clean and after each injected tamper.
Exit 0 only if the clean store passes and every tamper is detected."""
import contextlib
import io
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from integrity import main as crosscheck_cli  # noqa: E402
from tests.memory_helpers import MEM_KEY  # noqa: E402
from tests.research_helpers import CLAIM_KEY, ResearchHarness, fixture  # noqa: E402
from tests.test_integrity import TAMPERS, run_research  # noqa: E402


def check(r) -> tuple[int, dict]:
    d = r.h.dir.name
    for name, key in (("m.key", MEM_KEY), ("c.key", CLAIM_KEY)):
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(key)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = crosscheck_cli(["--audit", r.h.audit_path, "--memory", r.h.mem_path, "--memory-key-file",
                               os.path.join(d, "m.key"), "--claims", r.claims_path, "--claims-key-file",
                               os.path.join(d, "c.key")])
    return code, json.loads(out.getvalue())


def main() -> int:
    fx = fixture()
    failures, results = [], {}
    try:
        for name in ["clean", *TAMPERS]:
            r = ResearchHarness(fx)
            r.claims_path = os.path.join(r.h.dir.name, "claims.sqlite")
            try:
                run_research(r)
                if name != "clean":
                    TAMPERS[name](r)
                code, rep = check(r)
                results[name] = code
                if (code == 0) != (name == "clean"):
                    failures.append(name)
                if name == "clean":
                    print(f"CROSSCHECK clean ok={rep['ok']} audit_records={rep['audit_records']} "
                          f"memory_rows={rep['stores']['memory']['rows']} claim_rows={rep['stores']['claims']['rows']}")
            finally:
                r.close()
    finally:
        fx.close()
    print(f"CROSSCHECK tampers={len(TAMPERS)} detected={sum(1 for n in TAMPERS if results.get(n) == 1)} "
          f"failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
