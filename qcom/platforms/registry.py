"""Platform name to adapter class. The one place downstream code may mention a platform name."""

from __future__ import annotations

from qcom.core.models import PLATFORM_NAMES
from qcom.platforms.base import PlatformAdapter
from qcom.platforms.fake.adapter import FakeAdapter

REGISTRY: dict[str, type[PlatformAdapter]] = {
    FakeAdapter.name: FakeAdapter,
}

#: Real platforms not yet implemented, with the phase that delivers each.
PLANNED: dict[str, str] = {
    "blinkit": "Phase 2",
    "swiggy_instamart": "Phase 3",
    "zepto": "Phase 3",
    "bigbasket": "Phase 3",
}


def implemented_platforms() -> list[str]:
    """Real platforms with a working adapter, in canonical order. Excludes the fake."""
    return [n for n in PLATFORM_NAMES if n in REGISTRY]


def get_adapter_class(name: str) -> type[PlatformAdapter]:
    try:
        return REGISTRY[name]
    except KeyError:
        if name in PLANNED:
            raise KeyError(f"platform {name!r} is not implemented yet ({PLANNED[name]})") from None
        raise KeyError(f"unknown platform {name!r}; known: {sorted(REGISTRY)}") from None


def resolve_platforms(requested: list[str] | None) -> list[str]:
    """Blank means every implemented real platform. Names are validated, order preserved, duplicates dropped."""
    if not requested:
        names = implemented_platforms()
        if not names:
            raise ValueError(
                "no real platform adapter is implemented yet; name one explicitly with --platforms "
                f"(available now: {sorted(REGISTRY)})"
            )
        return names
    seen: list[str] = []
    for raw in requested:
        name = raw.strip().lower()
        if not name:
            continue
        get_adapter_class(name)  # raises with a precise message
        if name not in seen:
            seen.append(name)
    return seen
