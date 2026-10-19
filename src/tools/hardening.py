"""Host-process hardening for running tool workers (Linux).

set_non_dumpable() calls prctl(PR_SET_DUMPABLE, 0) on the *calling* process. Same-UID
processes, including tool workers, can then no longer read its /proc/<pid>/environ or
/proc/<pid>/mem, or ptrace it. Call it once at control-plane host startup. It is not
applied implicitly on import because it changes process-wide debugging behaviour.
Returns False when unsupported (non-Linux, or libc/prctl unavailable).
"""

import ctypes
import ctypes.util
import sys

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3


def set_non_dumpable() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            return False
        return libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False
