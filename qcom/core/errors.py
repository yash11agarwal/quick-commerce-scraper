"""Error codes and the exceptions that carry them.

Every failure in qcom ends as exactly one ``ErrorCode``. The codes are the ones in
docs/REQUIREMENTS.md section 6, verbatim, plus the one skip code that section names.
Nothing else may be used as a failure state, and no code path may swallow an exception to
make a run look clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    BLOCKED = "BLOCKED"
    PROXY_ERROR = "PROXY_ERROR"
    LOCATION_NOT_SET = "LOCATION_NOT_SET"
    NO_RESULTS = "NO_RESULTS"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    PARSE_ERROR = "PARSE_ERROR"
    UNKNOWN = "UNKNOWN"
    SKIPPED_PLATFORM_BLOCKED = "SKIPPED_PLATFORM_BLOCKED"


#: Codes the policy table allows to retry. Everything else is terminal on first sight.
RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.NETWORK_TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.PROXY_ERROR,
        ErrorCode.LOCATION_NOT_SET,
        ErrorCode.UNKNOWN,
    }
)


class QcomError(Exception):
    """Base for every typed failure. Subclasses fix ``code``."""

    code: ErrorCode = ErrorCode.UNKNOWN

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})


class NetworkTimeoutError(QcomError):
    code = ErrorCode.NETWORK_TIMEOUT


class RateLimitedError(QcomError):
    code = ErrorCode.RATE_LIMITED


class BlockedError(QcomError):
    code = ErrorCode.BLOCKED


class ProxyError(QcomError):
    code = ErrorCode.PROXY_ERROR


class LocationNotSetError(QcomError):
    code = ErrorCode.LOCATION_NOT_SET


class SchemaDriftError(QcomError):
    """A structural path the platform spec says is always present is missing or mistyped."""

    code = ErrorCode.SCHEMA_DRIFT

    def __init__(self, message: str, *, path: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"{message} [path: {path}]", detail={**(detail or {}), "path": path})
        self.path = path


class ParseError(QcomError):
    code = ErrorCode.PARSE_ERROR


class UnknownError(QcomError):
    code = ErrorCode.UNKNOWN


class RunAbortedError(Exception):
    """The whole run must stop (for example the proxy is dead and nothing is left to rotate to)."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.UNKNOWN) -> None:
        super().__init__(message)
        self.code = code


class ConfigError(Exception):
    """config.yaml or .env is unusable. Named after the key that is wrong."""


@dataclass(frozen=True)
class InputProblem:
    sheet: str
    cell: str
    message: str

    def __str__(self) -> str:
        return f"{self.sheet}!{self.cell}: {self.message}"


class InputValidationError(Exception):
    """The input workbook failed validation. Carries every problem found, not just the first."""

    def __init__(self, problems: list[InputProblem]) -> None:
        self.problems = list(problems)
        lines = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(f"input workbook has {len(self.problems)} problem(s):\n{lines}")


def code_of(exc: BaseException) -> ErrorCode | None:
    """The error code an exception carries, or None if it is not a typed qcom failure."""
    if isinstance(exc, QcomError):
        return exc.code
    return None
