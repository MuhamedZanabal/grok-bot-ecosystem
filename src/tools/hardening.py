"""Compatibility re-export; host hardening lives in control_plane.hardening and is
enforced by ControlPlane construction since cycle-004."""

from control_plane.hardening import is_non_dumpable, set_non_dumpable

__all__ = ["is_non_dumpable", "set_non_dumpable"]
