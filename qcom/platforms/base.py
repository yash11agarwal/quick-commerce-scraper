"""The adapter contract. The run loop calls exactly these five functions and nothing else."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from qcom.core.errors import ErrorCode, ParseError, SchemaDriftError
from qcom.core.models import EffectiveLocation, HealthReport, LocationExpectation, Probe, ProductListing, RawCapture


class PlatformAdapter(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    hosts: ClassVar[tuple[str, ...]]
    probe: ClassVar[Probe]
    needs_browser: ClassVar[bool] = True
    #: True when the platform exposes an integer stock count (Blinkit, Zepto). False means
    #: stock_qty must be None on every row (Swiggy Instamart, BigBasket).
    stock_depth: ClassVar[bool] = False

    def __init__(self, *, navigation_timeout_s: float = 45.0) -> None:
        self.navigation_timeout_s = navigation_timeout_s

    @abstractmethod
    def set_location(self, page: Any, pincode: str, expectation: LocationExpectation) -> EffectiveLocation:
        """Apply the pincode, read the location back, return what the site has in effect, or raise LocationNotSetError."""

    @abstractmethod
    def search(self, page: Any, term: str, max_results: int) -> list[RawCapture]:
        """Run the primary strategy and return every raw capture. No parsing here."""

    @abstractmethod
    def parse(self, raw: RawCapture) -> list[ProductListing]:
        """Pure. No network, no browser, no clock. Missing structure raises SchemaDriftError."""

    @abstractmethod
    def classify_failure(self, exc_or_response: Any) -> ErrorCode | None:
        """Map a platform-specific signal to a code, or None to defer to the generic classifier."""

    @abstractmethod
    def health_check(self, page: Any) -> HealthReport:
        """Run the probe and assert the documented shape still holds."""


# ----------------------------------------------------------------------------- parser helpers


def load_json(raw: RawCapture) -> Any:
    try:
        return json.loads(raw.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"capture is not valid JSON: {exc}", detail={"capture_id": raw.capture_id}) from exc


def require(obj: Any, path: str, *, expect: type | tuple[type, ...] | None = None, base: str = "") -> Any:
    """Walk ``a.b[0].c`` and raise SchemaDriftError naming the path if any step is missing or mistyped.

    ``base`` is the path of ``obj`` inside the whole payload, so a drift deep inside a row is
    reported as ``snippets[5].data.inventory`` rather than ``inventory``. A ``None`` value passes
    the type check (absent-by-design values are the caller's decision); a missing key never does.
    """
    node = obj
    walked = base
    for step in _steps(path):
        if isinstance(step, int):
            if not isinstance(node, list) or step >= len(node):
                raise SchemaDriftError("expected list element", path=f"{walked}[{step}]")
            node = node[step]
            walked += f"[{step}]"
        else:
            if not isinstance(node, dict) or step not in node:
                raise SchemaDriftError("expected key", path=f"{walked}.{step}" if walked else step)
            node = node[step]
            walked = f"{walked}.{step}" if walked else step
    if expect is not None and node is not None and not isinstance(node, expect):
        raise SchemaDriftError(f"expected {expect}, got {type(node).__name__}", path=walked)
    return node


def _steps(path: str) -> list[str | int]:
    out: list[str | int] = []
    for part in path.split("."):
        while "[" in part:
            head, _, rest = part.partition("[")
            if head:
                out.append(head)
            idx, _, part = rest.partition("]")
            out.append(int(idx))
        if part:
            out.append(part)
    return out
