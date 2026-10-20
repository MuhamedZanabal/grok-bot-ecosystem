"""CI step (D-0024): seed a scheduler + economics state with every attention class, run the
operator status CLI (`python -m economics status`) in JSON and Markdown, and require that
each class is reported. Exit 0 iff all are listed."""
import io
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
from tests.test_economics import StatusTests  # noqa: E402

if __name__ == "__main__":
    buf = io.StringIO()
    res = unittest.TextTestRunner(stream=buf, verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName("test_status_report_lists_every_attention_class", StatusTests))
    print("STATUS_CI ok=%s" % res.wasSuccessful())
    sys.exit(0 if res.wasSuccessful() else 1)
