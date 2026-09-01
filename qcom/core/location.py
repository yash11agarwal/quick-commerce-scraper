"""Choosing the right autocomplete suggestion and judging a location readback.

Every playbook found that typing 700048 offered a decoy suggestion in Madhya Pradesh. The
adapters therefore never take suggestion index 0. This module gives them a deterministic rule
that needs no user input: the first two digits of an Indian pincode identify the postal zone,
and the zone identifies the state (or the short list of states that share it). A suggestion
that names a different state is rejected; a readback address that names a different state
fails verification. An optional city or state from the input workbook makes the rule
stricter. Nothing in here produces an output data value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from qcom.core.errors import LocationNotSetError
from qcom.core.models import LocationExpectation

# India Post PIN zones: first two digits -> states and union territories served. Generous on
# purpose: every state that shares a prefix is listed, so the table can only reject a
# suggestion that names a state which is impossible for that prefix.
_ZONES: dict[str, tuple[str, ...]] = {
    "11": ("Delhi",),
    "12": ("Haryana",), "13": ("Haryana",),
    "14": ("Punjab",), "15": ("Punjab",), "16": ("Punjab", "Chandigarh"),
    "17": ("Himachal Pradesh",),
    "18": ("Jammu and Kashmir", "Ladakh"), "19": ("Jammu and Kashmir", "Ladakh"),
    "20": ("Uttar Pradesh",), "21": ("Uttar Pradesh",), "22": ("Uttar Pradesh",), "23": ("Uttar Pradesh",),
    "24": ("Uttar Pradesh", "Uttarakhand"), "25": ("Uttar Pradesh", "Uttarakhand"), "26": ("Uttar Pradesh", "Uttarakhand"),
    "27": ("Uttar Pradesh",), "28": ("Uttar Pradesh",),
    "30": ("Rajasthan",), "31": ("Rajasthan",), "32": ("Rajasthan",), "33": ("Rajasthan",), "34": ("Rajasthan",),
    "36": ("Gujarat",), "37": ("Gujarat",), "38": ("Gujarat",),
    "39": ("Gujarat", "Dadra and Nagar Haveli and Daman and Diu"),
    "40": ("Maharashtra", "Goa"), "41": ("Maharashtra",), "42": ("Maharashtra",), "43": ("Maharashtra",), "44": ("Maharashtra",),
    "45": ("Madhya Pradesh",), "46": ("Madhya Pradesh",), "47": ("Madhya Pradesh",), "48": ("Madhya Pradesh",),
    "49": ("Chhattisgarh",),
    "50": ("Telangana", "Andhra Pradesh"), "51": ("Andhra Pradesh", "Telangana"),
    "52": ("Andhra Pradesh", "Telangana"), "53": ("Andhra Pradesh", "Telangana", "Puducherry"),
    "56": ("Karnataka",), "57": ("Karnataka",), "58": ("Karnataka",), "59": ("Karnataka",),
    "60": ("Tamil Nadu", "Puducherry"), "61": ("Tamil Nadu",), "62": ("Tamil Nadu",), "63": ("Tamil Nadu",), "64": ("Tamil Nadu",),
    "67": ("Kerala", "Puducherry"), "68": ("Kerala", "Lakshadweep"), "69": ("Kerala",),
    "70": ("West Bengal",), "71": ("West Bengal",), "72": ("West Bengal",),
    "73": ("West Bengal", "Sikkim"), "74": ("West Bengal", "Andaman and Nicobar Islands"),
    "75": ("Odisha",), "76": ("Odisha",), "77": ("Odisha",),
    "78": ("Assam",),
    "79": ("Arunachal Pradesh", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Tripura", "Assam"),
    "80": ("Bihar", "Jharkhand"), "81": ("Bihar", "Jharkhand"), "82": ("Bihar", "Jharkhand"),
    "83": ("Bihar", "Jharkhand"), "84": ("Bihar", "Jharkhand"), "85": ("Bihar", "Jharkhand"),
    "90": ("Army Postal Service",), "91": ("Army Postal Service",), "92": ("Army Postal Service",),
    "93": ("Army Postal Service",), "94": ("Army Postal Service",), "95": ("Army Postal Service",),
    "96": ("Army Postal Service",), "97": ("Army Postal Service",), "98": ("Army Postal Service",), "99": ("Army Postal Service",),
}

# Spellings seen in addresses, mapped to the canonical names used in _ZONES.
_STATE_ALIASES: dict[str, str] = {
    "andaman and nicobar islands": "Andaman and Nicobar Islands",
    "andaman & nicobar": "Andaman and Nicobar Islands",
    "andhra pradesh": "Andhra Pradesh",
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chandigarh": "Chandigarh",
    "chhattisgarh": "Chhattisgarh",
    "chattisgarh": "Chhattisgarh",
    "dadra and nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "delhi": "Delhi",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jammu and kashmir": "Jammu and Kashmir",
    "jammu & kashmir": "Jammu and Kashmir",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "ladakh": "Ladakh",
    "lakshadweep": "Lakshadweep",
    "madhya pradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "tamilnadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "uttarakhand": "Uttarakhand",
    "uttaranchal": "Uttarakhand",
    "west bengal": "West Bengal",
}

# Longest alias first so "dadra and nagar haveli and daman and diu" beats "daman and diu".
_ALIAS_PATTERNS = [
    (re.compile(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", re.IGNORECASE), canonical)
    for alias, canonical in sorted(_STATE_ALIASES.items(), key=lambda kv: -len(kv[0]))
]


def expected_states(pincode: str) -> tuple[str, ...]:
    """States a pincode can belong to, from its first two digits. Empty if the prefix is unknown."""
    return _ZONES.get(pincode[:2], ())


def find_states(text: str) -> tuple[str, ...]:
    """Canonical state names mentioned in free text, in order of first appearance, de-duplicated."""
    found: list[tuple[int, str]] = []
    for pattern, canonical in _ALIAS_PATTERNS:
        m = pattern.search(text)
        if m and canonical not in {c for _, c in found}:
            found.append((m.start(), canonical))
    return tuple(c for _, c in sorted(found))


def make_expectation(
    pincode: str,
    city: str | None = None,
    state: str | None = None,
    exclude_suggestions: tuple[str, ...] = (),
) -> LocationExpectation:
    states = list(expected_states(pincode))
    if state:
        canonical = _STATE_ALIASES.get(state.strip().lower(), state.strip())
        states = [canonical]  # a user-supplied state overrides the zone table
    return LocationExpectation(
        pincode=pincode,
        city=city.strip() if city else None,
        state=state.strip() if state else None,
        expected_states=tuple(states),
        exclude_suggestions=tuple(exclude_suggestions),
    )


@dataclass
class SuggestionChoice:
    index: int
    text: str
    ambiguous: bool
    candidates: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


def choose_suggestion(texts: list[str], expectation: LocationExpectation) -> SuggestionChoice:
    """Pick the suggestion to click. Raises LocationNotSetError when nothing is acceptable.

    Rules, in order:
    1. the text must contain the pincode and must not have been excluded by an earlier attempt
    2. a text that names a state impossible for this pincode is rejected
    3. texts naming the expected city are preferred; failing that, texts naming an expected state
    4. among what remains, the first wins; more than one is flagged ambiguous (the readback
       check is the final arbiter, and a failed readback feeds the text back as an exclusion)
    """
    pin = expectation.pincode
    candidates = [(i, t) for i, t in enumerate(texts) if pin in t and t not in expectation.exclude_suggestions]
    if not candidates:
        raise LocationNotSetError(
            f"no suggestion contains pincode {pin}",
            detail={"suggestions": texts, "excluded": list(expectation.exclude_suggestions)},
        )

    rejected: list[tuple[str, str]] = []
    allowed: list[tuple[int, str]] = []
    for i, t in candidates:
        states = find_states(t)
        if expectation.expected_states and states and not set(states) & set(expectation.expected_states):
            rejected.append((t, f"names {', '.join(states)}; pincode {pin} is in {', '.join(expectation.expected_states)}"))
        else:
            allowed.append((i, t))
    if not allowed:
        raise LocationNotSetError(
            f"every suggestion for {pin} names a state that pincode cannot be in: "
            + "; ".join(f"{t!r} {why}" for t, why in rejected),
            detail={"suggestions": texts, "rejected": rejected},
        )

    city_hits = [(i, t) for i, t in allowed if expectation.city and expectation.city.lower() in t.lower()]
    state_hits = [(i, t) for i, t in allowed if set(find_states(t)) & set(expectation.expected_states)]
    pool = city_hits or state_hits or allowed
    index, text = pool[0]
    return SuggestionChoice(
        index=index,
        text=text,
        ambiguous=len(pool) > 1,
        candidates=[t for _, t in pool],
        rejected=rejected,
    )


@dataclass
class ReadbackCheck:
    ok: bool
    pincode_found: bool
    states_found: tuple[str, ...]
    reason: str


def check_readback(text: str | None, expectation: LocationExpectation) -> ReadbackCheck:
    """Judge a location string the platform reported (header text, address field)."""
    if not text:
        return ReadbackCheck(False, False, (), "platform reported no location text")
    pin_ok = expectation.pincode in text
    states = find_states(text)
    if not pin_ok:
        return ReadbackCheck(False, False, states, f"readback {text!r} does not contain {expectation.pincode}")
    if expectation.expected_states and states and not set(states) & set(expectation.expected_states):
        return ReadbackCheck(
            False, True, states,
            f"readback names {', '.join(states)} but pincode {expectation.pincode} is in {', '.join(expectation.expected_states)}",
        )
    return ReadbackCheck(True, True, states, "ok")


def extract_pincode(text: str | None, expected: str) -> str | None:
    """The six-digit pincode present in a readback string, if it is the expected one."""
    if not text:
        return None
    return expected if expected in text else None
