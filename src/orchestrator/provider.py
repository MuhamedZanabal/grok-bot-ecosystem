"""Provider-neutral model interface.

A provider receives one canonical-JSON string and returns one string. It is given no
objects, handles or callables, so model output can influence the system only through
the text it returns. Real adapters (any vendor) implement `complete`; Cycle 2 ships only
the deterministic MockProvider and makes no network calls.
"""

import time
from typing import Iterable, Protocol, runtime_checkable


class ProviderError(Exception):
    code = "provider_error"


class ProviderTimeout(ProviderError):
    code = "provider_timeout"


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def complete(self, request_json: str) -> str: ...


class MockProvider:
    """Deterministic scripted provider for tests.

    Each script item is consumed in order by one `complete` call:
      str                 -> returned as the model output
      Exception instance  -> raised
      ("sleep", s, item)  -> sleep `s` seconds, then handle `item`
    An exhausted script raises ProviderUnavailable.
    """

    def __init__(self, script: Iterable[object], name: str = "mock") -> None:
        self.name = name
        self._script = list(script)
        self.calls: list[object] = []

    def complete(self, request_json: str) -> str:
        self.calls.append(request_json)
        if not self._script:
            raise ProviderUnavailable("script_exhausted")
        item = self._script.pop(0)
        if isinstance(item, tuple) and len(item) == 3 and item[0] == "sleep":
            time.sleep(item[1])
            item = item[2]
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]
