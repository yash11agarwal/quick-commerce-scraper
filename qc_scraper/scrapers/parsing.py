"""Shared helpers for digging product data out of internal API payloads.

These payloads are deeply nested, undocumented, and reshuffled regularly.
Two defensive techniques are used throughout the adapters:

- ``iter_dicts_with_keys``: recursively walk arbitrary JSON and yield every
  dict that looks like a product node, instead of hard-coding the exact
  nesting path (which breaks on every layout change).
- ``estimate_from_label``: turn scarcity strings like "Only 2 left" into an
  integer *estimate* (never a real count — see schema.StockGranularity).
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional

# "Only 2 left", "only 1 left in stock", "2 left" ...
_LEFT_RE = re.compile(r"only\s*(\d+)\s*left|(\d+)\s*left\b", re.IGNORECASE)


def iter_dicts_with_keys(node: Any, required_any: set[str], *, max_depth: int = 25) -> Iterator[dict]:
    """Yield every dict in an arbitrarily nested JSON structure that contains
    at least one key from ``required_any``. Depth-limited to be cycle-safe."""
    if max_depth < 0:
        return
    if isinstance(node, dict):
        if required_any & node.keys():
            yield node
        for value in node.values():
            yield from iter_dicts_with_keys(value, required_any, max_depth=max_depth - 1)
    elif isinstance(node, list):
        for item in node:
            yield from iter_dicts_with_keys(item, required_any, max_depth=max_depth - 1)


def first_key(d: dict, *keys: str) -> Any:
    """Return the first non-None value among candidate key spellings."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def dig(node: Any, *path: str) -> Any:
    """Null-safe nested access: dig(d, 'a', 'b', 'c') == d['a']['b']['c'] or None."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def estimate_from_label(label: Optional[str]) -> Optional[int]:
    """Extract N from scarcity labels like 'Only 2 left'. Returns None if absent."""
    if not label:
        return None
    m = _LEFT_RE.search(str(label))
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def find_stock_label(d: dict) -> Optional[str]:
    """Look for a scarcity/availability label in common field spellings."""
    candidates = (
        "inventory_label", "stock_label", "availability_label", "offer_tag",
        "label", "unavailable_text", "out_of_stock_text",
    )
    for key in candidates:
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None
