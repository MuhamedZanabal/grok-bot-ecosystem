"""Store integrity cross-checks against hash-chained audit anchors (cycle-008, D-0022)."""

from .crosscheck import crosscheck, main

__all__ = ["crosscheck", "main"]
