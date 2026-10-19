"""Global kill switch. Engaged in memory or by the presence of a file (shared across
processes). Fails closed: if the switch file's state cannot be determined, it is engaged."""

import os
import tempfile


class KillSwitch:
    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._engaged = False

    def engaged(self) -> bool:
        if self._engaged:
            return True
        if self._path is None:
            return False
        try:
            os.stat(self._path)
            return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return True  # fail closed: unknowable state counts as engaged

    def engage(self, reason: str = "") -> None:
        self._engaged = True
        if self._path is not None:
            d = os.path.dirname(os.path.abspath(self._path))
            fd, tmp = tempfile.mkstemp(dir=d)
            with os.fdopen(fd, "w") as fh:
                fh.write(reason)
            os.replace(tmp, self._path)

    def release(self) -> None:
        self._engaged = False
        if self._path is not None:
            try:
                os.remove(self._path)
            except FileNotFoundError:
                pass
