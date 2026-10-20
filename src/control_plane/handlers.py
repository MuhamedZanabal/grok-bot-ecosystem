"""Handler kinds the control plane will bind.

By default ControlPlane only binds IsolatedHandler instances: objects that run tool code
outside this process (see tools.subprocess_handler). In-process callables are allowed
only when the ControlPlane is constructed with allow_inprocess_handlers=True (tests,
trusted built-ins), and that choice is audited at bind time.
"""


class IsolatedHandler:
    """Marker base class: calling it must execute tool code out of process."""

    __slots__ = ()
    isolated = True

    def __call__(self, args: dict):  # pragma: no cover - abstract
        raise NotImplementedError


class TrustedHostHandler:
    """Marker base class for repository-owned handlers that run in the host process and
    execute NO tool- or model-supplied code (e.g. the memory store). They are part of the
    TCB, bound by default, and audited at bind with isolation="trusted_host" (D-0018)."""

    __slots__ = ()
    isolated = False
    isolation = "trusted_host"

    def __call__(self, args: dict):  # pragma: no cover - abstract
        raise NotImplementedError


class HandlerTimeout(Exception):
    """Raised by an isolated handler after it has killed a worker that exceeded its deadline."""


class HandlerFailure(Exception):
    """Raised by an isolated handler for a non-timeout failure with a stable `code`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
