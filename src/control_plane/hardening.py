"""Host-process hardening (Linux).

set_non_dumpable() calls prctl(PR_SET_DUMPABLE, 0) on the calling process. Same-UID
processes can then no longer read its /proc/<pid>/environ or /proc/<pid>/mem, or ptrace it.
Since cycle-004 ControlPlane() calls this and refuses to construct if it fails
(defence in depth: sandboxed workers cannot see host /proc at all).
Returns False when unsupported (non-Linux, or libc/prctl unavailable).
"""

import ctypes
import ctypes.util
import sys

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3


def _libc():
    return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


def is_non_dumpable() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        return _libc().prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def set_non_dumpable() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = _libc()
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            return False
        return libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False
