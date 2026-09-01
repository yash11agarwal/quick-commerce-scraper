"""Blinkit adapter. Every step here is one the spec (docs/platform-specs/blinkit.md) lists.

Location: header picker, typed pincode, suggestion chosen by ``core.location.choose_suggestion``
(never index 0), page reload, header read back and asserted (rule 4).
Search: header input (or the ``/s/?q=`` route as a logged navigation fallback), scroll until
the snippet count stops growing, then ``JSON.stringify`` of the Redux search slice, stored as
``page_state``. Every JSON response from a blinkit.com host during the search is stored too,
as unparsed ``network_response`` evidence, which is how the search XHR URL (OPEN) gets learned.
Parsing lives in ``parser.py`` and touches nothing here.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from qcom.core.clock import now_utc
from qcom.core.errors import (
    BlockedError,
    ErrorCode,
    LocationNotSetError,
    QcomError,
    RateLimitedError,
    SchemaDriftError,
    UnknownError,
)
from qcom.core.location import check_readback, choose_suggestion, make_expectation
from qcom.core.models import (
    CaptureSource,
    EffectiveLocation,
    HealthCheck,
    HealthReport,
    LocationExpectation,
    Probe,
    ProductListing,
    RawCapture,
)
from qcom.platforms.base import PlatformAdapter
from qcom.platforms.blinkit import parser

HOME_URL = "https://blinkit.com"
SEARCH_URL = "https://blinkit.com/s/?q={term}"
HOST = "blinkit.com"
LOCATION_PLACEHOLDER = "search delivery location"
SEARCH_PLACEHOLDER = "Search for atta dal and more"
STRATEGY_PRIMARY = "redux_store"
STRATEGY_EVIDENCE = "network_capture"
STRATEGY_LOCATION_EVIDENCE = "location_evidence"
KNOWN_MERCHANT_700048 = "30872"

_ETA_RE = re.compile(r"Delivery in (\d+) minutes?", re.IGNORECASE)
_UNAVAILABLE_RE = re.compile(r"Currently unavailable", re.IGNORECASE)
_SELECT_RE = re.compile(r"Select Location", re.IGNORECASE)
_MAX_NETWORK_CAPTURES = 200
_NAVIGATION_RACE_MARKERS = ("Execution context was destroyed", "navigating and changing the content", "Cannot find context with specified id")

# Deepest elements whose text carries the ETA or "Select Location" label, then the smallest
# ancestor (up to eight levels) whose text also carries the pincode: that is the readback.
_READBACK_JS = """
(pincode) => {
  const norm = t => (t || "").replace(/\\s+/g, " ").trim();
  const isLabel = t => /Delivery in \\d+ minutes?/i.test(t) || /Currently unavailable/i.test(t) || /Select Location/i.test(t);
  const all = Array.from(document.querySelectorAll("body *"));
  const anchors = all.filter(e => isLabel(norm(e.innerText)) && !Array.from(e.children).some(c => isLabel(norm(c.innerText))));
  let labelText = null, readback = null;
  if (anchors.length) {
    const a = anchors[0];
    labelText = norm(a.innerText);
    let node = a;
    for (let i = 0; i < 8 && node && node !== document.body; i++) {
      const t = norm(node.innerText);
      if (t.includes(pincode)) { readback = t.slice(0, 600); break; }
      node = node.parentElement;
    }
  }
  const header = document.querySelector("header");
  return {
    label_text: labelText,
    readback_text: readback,
    header_text: header ? norm(header.innerText).slice(0, 600) : null,
    anchor_count: anchors.length,
    title: document.title,
    url: location.href,
  };
}
"""

# Deepest elements whose text carries the pincode (inputs excluded), each widened to the
# highest ancestor that still contains exactly one such element, so a suggestion made of two
# spans ("Patipukur" + "Kolkata, West Bengal 700048, India") is read whole while the list
# container, which holds every suggestion, is never a candidate. Each candidate is marked with
# a data attribute so the click lands on that element and nothing else.
_SUGGESTIONS_JS = """
(pincode) => {
  const norm = t => (t || "").replace(/\\s+/g, " ").trim();
  const skip = new Set(["INPUT", "TEXTAREA", "SCRIPT", "STYLE", "SELECT", "OPTION"]);
  document.querySelectorAll("[data-qcom-suggestion]").forEach(e => e.removeAttribute("data-qcom-suggestion"));
  const all = Array.from(document.querySelectorAll("body *")).filter(e => !skip.has(e.tagName));
  const has = e => norm(e.innerText).includes(pincode);
  const deepest = all.filter(e => has(e) && !Array.from(e.children).some(has));
  const count = e => deepest.filter(d => e === d || e.contains(d)).length;
  const out = [];
  deepest.forEach((d, i) => {
    let node = d;
    for (let k = 0; k < 4; k++) {
      const p = node.parentElement;
      if (!p || p === document.body || count(p) !== 1) break;
      node = p;
    }
    node.setAttribute("data-qcom-suggestion", String(i));
    out.push({ index: i, text: norm(node.innerText).slice(0, 300), tag: node.tagName, role: node.getAttribute("role") });
  });
  return out;
}
"""

_STORE_PRESENT_JS = "() => typeof window.__reduxStore__ !== 'undefined' && typeof window.__reduxStore__.getState === 'function'"

_SEARCH_PROGRESS_JS = """
() => {
  try {
    const s = window.__reduxStore__.getState();
    const slice = s && s.ui && s.ui.search ? s.ui.search.searchProductBffData : undefined;
    if (!slice || !Array.isArray(slice.snippets)) return { present: false, snippets: 0, products: 0 };
    const products = slice.snippets.filter(x => x && x.data && x.data.product_id !== undefined && x.data.product_id !== null).length;
    return { present: true, snippets: slice.snippets.length, products };
  } catch (e) { return { present: false, snippets: 0, products: 0, error: String(e) }; }
}
"""

# The slice is serialised in the page so the stored bytes are exactly what the store held.
_SEARCH_SLICE_JS = """
() => {
  const s = window.__reduxStore__.getState();
  const slice = s && s.ui && s.ui.search ? s.ui.search.searchProductBffData : undefined;
  return JSON.stringify({ searchProductBffData: slice === undefined ? null : slice });
}
"""

_LOCATION_SLICES_JS = """
() => {
  const pick = (obj, key) => { try { return JSON.parse(JSON.stringify(obj ? obj[key] : undefined) ?? "null"); } catch (e) { return { "_unserialisable": String(e) }; } };
  try {
    const s = window.__reduxStore__.getState();
    const d = s ? s.data : undefined;
    return JSON.stringify({ location: pick(d, "location"), merchant: pick(d, "merchant"), eta: pick(d, "eta"), addressesV2: pick(d, "addressesV2"), chainId: pick(d, "chainId") });
  } catch (e) { return JSON.stringify({ "_error": String(e) }); }
}
"""


class BlinkitAdapter(PlatformAdapter):
    name = "blinkit"
    version = "2.0.0-p2"
    hosts = (HOST,)
    probe = Probe(pincode="700048", term="Mango", city="Kolkata", state="West Bengal")
    needs_browser = True
    stock_depth = True

    #: how long to wait for the autocomplete list, the header readback and the search slice
    ui_wait_s: float = 15.0
    results_wait_s: float = 20.0
    scroll_step_px: int = 3000
    scroll_pause_ms: int = 800
    max_scroll_steps: int = 12
    poll_ms: int = 300

    # ------------------------------------------------------------------ navigation helpers

    def _goto(self, page: Any, url: str) -> Any:
        # a navigation timeout propagates as is; the generic classifier maps it to NETWORK_TIMEOUT
        response = page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_s * 1000)
        self._check_document(response, url)
        return response

    @staticmethod
    def _check_document(response: Any, url: str) -> None:
        BlinkitAdapter._check_status(getattr(response, "status", None), url)

    @staticmethod
    def _check_status(status: int | None, url: str) -> None:
        if status is None:
            return
        if status == 403:
            raise BlockedError(f"HTTP 403 on the document {url}", detail={"url": url, "status": status})
        if status == 429:
            raise RateLimitedError(f"HTTP 429 on the document {url}", detail={"url": url, "status": status})
        if status >= 500:
            raise UnknownError(f"HTTP {status} on the document {url}", detail={"url": url, "status": status})

    def _eval(self, page: Any, js: str, arg: Any = None) -> Any:
        """``page.evaluate`` that survives the page reloading underneath it (retried, bounded)."""
        for attempt in range(4):
            try:
                return page.evaluate(js, arg) if arg is not None else page.evaluate(js)
            except PlaywrightError as exc:
                text = str(exc)
                if attempt == 3 or not any(m in text for m in _NAVIGATION_RACE_MARKERS):
                    raise
                page.wait_for_timeout(self.poll_ms)
        raise AssertionError("unreachable")

    def _poll(self, page: Any, js: str, arg: Any, *, until: Any, timeout_s: float) -> Any:
        """Evaluate ``js`` until ``until(result)`` is true or the time is up. Returns the last result."""
        deadline = time.monotonic() + timeout_s
        result = self._eval(page, js, arg)
        while not until(result) and time.monotonic() < deadline:
            page.wait_for_timeout(self.poll_ms)
            result = self._eval(page, js, arg)
        return result

    # ------------------------------------------------------------------ location

    def set_location(self, page: Any, pincode: str, expectation: LocationExpectation) -> EffectiveLocation:
        self._goto(page, HOME_URL)
        evidence: dict[str, Any] = {"flow": []}

        # A context restored from a session jar may already carry the location. Rule 4 still
        # applies: the header is read back and judged; nothing is assumed from the jar.
        info = self._poll(page, _READBACK_JS, pincode, until=lambda r: r["anchor_count"] > 0, timeout_s=self.ui_wait_s)
        evidence["initial_header"] = {k: info.get(k) for k in ("label_text", "readback_text", "header_text")}
        if info["label_text"] and (_ETA_RE.search(info["label_text"]) or _UNAVAILABLE_RE.search(info["label_text"])):
            text = info["readback_text"] or info["header_text"]
            if check_readback(text, expectation).ok:
                evidence["flow"].append("header_already_verified")
                return self._effective(pincode, info, expectation, evidence)
            evidence["flow"].append("header_present_but_not_verified")

        # Header picker -> typed pincode -> autocomplete -> chosen suggestion -> reload.
        self._open_picker(page, info, evidence)
        try:
            box = page.get_by_placeholder(LOCATION_PLACEHOLDER).first
            box.click(timeout=self.ui_wait_s * 1000)
            box.type(pincode, delay=60)
        except PlaywrightTimeout as exc:
            raise LocationNotSetError(
                f"location input with placeholder {LOCATION_PLACEHOLDER!r} not found: {str(exc).splitlines()[0]}",
                detail={"evidence": evidence, "url": page.url},
            ) from exc
        evidence["flow"].append("pincode_typed")

        suggestions = self._wait_for_suggestions(page, pincode)
        texts = [s["text"] for s in suggestions]
        evidence["suggestions"] = texts
        if not suggestions:
            raise LocationNotSetError(
                f"no autocomplete suggestion containing {pincode} appeared within {self.ui_wait_s:.0f}s",
                detail={"suggestions": [], "evidence": evidence},
            )
        choice = choose_suggestion(texts, expectation)  # raises LocationNotSetError, never index 0
        evidence["chosen_suggestion"] = choice.text
        evidence["ambiguous"] = choice.ambiguous
        evidence["rejected_suggestions"] = choice.rejected
        try:
            page.locator(f'[data-qcom-suggestion="{suggestions[choice.index]["index"]}"]').first.click(timeout=self.ui_wait_s * 1000)
        except PlaywrightTimeout as exc:
            raise LocationNotSetError(
                f"could not click suggestion {choice.text!r}: {str(exc).splitlines()[0]}",
                detail={"chosen_suggestion": choice.text, "suggestions": texts, "evidence": evidence},
            ) from exc
        evidence["flow"].append("suggestion_clicked")

        # The page reloads with the location applied (spec section 2 step 6).
        try:
            page.wait_for_load_state("domcontentloaded", timeout=self.navigation_timeout_s * 1000)
        except PlaywrightTimeout as exc:
            raise LocationNotSetError(
                f"page did not reload after choosing {choice.text!r}",
                detail={"chosen_suggestion": choice.text, "suggestions": texts, "evidence": evidence},
            ) from exc
        info = self._poll(
            page, _READBACK_JS, pincode,
            until=lambda r: bool(r["readback_text"]) or bool(r["label_text"] and not _SELECT_RE.search(r["label_text"]) and r["anchor_count"] > 0 and r["header_text"] and pincode in r["header_text"]),
            timeout_s=self.ui_wait_s,
        )
        evidence["post_reload_header"] = {k: info.get(k) for k in ("label_text", "readback_text", "header_text")}
        text = info["readback_text"] or (info["header_text"] if info["header_text"] and pincode in info["header_text"] else None)
        readback = check_readback(text, expectation)
        evidence["readback"] = readback.reason
        if not readback.ok:
            raise LocationNotSetError(
                f"header after reload does not verify {pincode}: {readback.reason}",
                detail={"chosen_suggestion": choice.text, "suggestions": texts, "evidence": evidence},
            )
        evidence["flow"].append("header_verified")
        return self._effective(pincode, info, expectation, evidence)

    def _open_picker(self, page: Any, info: dict[str, Any], evidence: dict[str, Any]) -> None:
        label = info.get("label_text") or ""
        pattern = _SELECT_RE if _SELECT_RE.search(label) or not label else re.compile(re.escape(label[:40]), re.IGNORECASE)
        try:
            page.get_by_text(pattern).first.click(timeout=self.ui_wait_s * 1000)
        except PlaywrightTimeout as exc:
            raise LocationNotSetError(
                f"delivery location selector not found in the header (label seen: {label!r}): {str(exc).splitlines()[0]}",
                detail={"evidence": evidence, "url": page.url, "title": info.get("title")},
            ) from exc
        evidence["flow"].append(f"picker_opened:{label or 'Select Location'}")

    def _wait_for_suggestions(self, page: Any, pincode: str) -> list[dict[str, Any]]:
        """Poll until at least one suggestion carries the pincode and the list has stopped changing."""
        deadline = time.monotonic() + self.ui_wait_s
        last: list[dict[str, Any]] = []
        stable = 0
        while time.monotonic() < deadline:
            page.wait_for_timeout(self.poll_ms)
            found = self._eval(page, _SUGGESTIONS_JS, pincode)
            if found and [f["text"] for f in found] == [f["text"] for f in last]:
                stable += 1
                if stable >= 2:
                    return found
            else:
                stable = 0
            last = found
        return last

    def _effective(self, pincode: str, info: dict[str, Any], expectation: LocationExpectation, evidence: dict[str, Any]) -> EffectiveLocation:
        label = info.get("label_text") or ""
        text = info.get("readback_text") or info.get("header_text") or ""
        eta_match = _ETA_RE.search(label) or _ETA_RE.search(text)
        eta = int(eta_match.group(1)) if eta_match else None
        if eta is None and (_UNAVAILABLE_RE.search(label) or _UNAVAILABLE_RE.search(text)):
            evidence["store_unavailable"] = True  # spec section 3: verified location, eta unknown
        evidence["header_text"] = text
        evidence["label_text"] = label
        return EffectiveLocation(
            platform=self.name,
            requested_pincode=pincode,
            effective_pincode=pincode if pincode in text else None,
            store_id=None,  # OPEN: no readback carries a store id; rows carry merchant_id
            eta_minutes=eta,
            address_text=text or None,
            evidence=evidence,
            verified_at_utc=now_utc(),
        )

    # ------------------------------------------------------------------ search

    def search(self, page: Any, term: str, max_results: int) -> list[RawCapture]:
        network: list[RawCapture] = []
        documents: list[tuple[int, str]] = []  # every HTML document a blinkit.com host served during the search
        request_meta: dict[str, Any] = {"term": term, "max_results": max_results}

        def on_response(response: Any) -> None:
            url = response.url
            if not urlsplit(url).netloc.endswith(HOST):
                return
            if response.request.resource_type == "document":
                documents.append((response.status, url))
            if len(network) >= _MAX_NETWORK_CAPTURES:
                return
            content_type = response.headers.get("content-type", "") if hasattr(response, "headers") else ""
            if "json" not in content_type.lower():
                return
            try:
                body = response.body()
            except PlaywrightError as exc:
                body = json.dumps({"_body_unavailable": str(exc)}).encode("utf-8")
            try:
                post_data = response.request.post_data
            except (PlaywrightError, UnicodeDecodeError):
                post_data = "<non-text body>"
            network.append(
                RawCapture(
                    platform=self.name,
                    strategy=STRATEGY_EVIDENCE,
                    source=CaptureSource.NETWORK_RESPONSE,
                    method=response.request.method,
                    url=url,
                    http_status=response.status,
                    content_type=content_type,
                    body=body,
                    captured_at_utc=now_utc(),
                    request={"header_names": sorted(response.request.headers.keys()), "post_data": post_data},
                    parse=False,
                )
            )

        page.on("response", on_response)
        try:
            request_meta["navigation"] = self._submit_search(page, term)
            progress = self._poll(page, _SEARCH_PROGRESS_JS, None, until=lambda r: r["present"] and r["snippets"] > 0, timeout_s=self.results_wait_s)
            for status, url in documents:
                self._check_status(status, url)  # a wall served after the header search is still a wall
            request_meta["document_statuses"] = [status for status, _ in documents]
            request_meta["scroll_steps"] = self._scroll_until_settled(page, progress, max_results)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeout:
                request_meta["network_idle"] = False
            else:
                request_meta["network_idle"] = True

            if not self._eval(page, _STORE_PRESENT_JS):
                raise UnknownError(
                    "page loaded without window.__reduxStore__ (spec section 10: classify this)",
                    detail={"url": page.url, "title": page.title(), "navigation": request_meta["navigation"]},
                )
            slice_text = self._eval(page, _SEARCH_SLICE_JS)
            evidence_text = self._eval(page, _LOCATION_SLICES_JS)
        finally:
            page.remove_listener("response", on_response)

        captured_at = now_utc()
        primary = RawCapture(
            platform=self.name,
            strategy=STRATEGY_PRIMARY,
            source=CaptureSource.PAGE_STATE,
            method="EVAL",
            url=page.url,
            http_status=None,
            content_type="application/json",
            body=slice_text.encode("utf-8"),
            captured_at_utc=captured_at,
            request=request_meta,
            parse=True,
        )
        location_evidence = RawCapture(
            platform=self.name,
            strategy=STRATEGY_LOCATION_EVIDENCE,
            source=CaptureSource.PAGE_STATE,
            method="EVAL",
            url=page.url,
            http_status=None,
            content_type="application/json",
            body=evidence_text.encode("utf-8"),
            captured_at_utc=captured_at,
            request={"slices": ["data.location", "data.merchant", "data.eta", "data.addressesV2", "data.chainId"]},
            parse=False,
        )
        return [primary, location_evidence, *network]

    def _submit_search(self, page: Any, term: str) -> str:
        """Header input when present, else the ``/s/?q=`` route. Returns which one was used."""
        box = page.get_by_placeholder(SEARCH_PLACEHOLDER)
        try:
            present = box.count() > 0
        except PlaywrightError:
            present = False
        if present:
            try:
                box.first.click(timeout=self.ui_wait_s * 1000)
                box = page.get_by_placeholder(SEARCH_PLACEHOLDER).first  # the click may have navigated to the search route
                box.fill("")
                box.type(term, delay=40)
                box.press("Enter")
                return "header_input"
            except PlaywrightTimeout:
                pass  # fall through to the documented route, logged in the capture's request metadata
        self._goto(page, SEARCH_URL.format(term=quote(term)))
        return "direct_url"

    def _scroll_until_settled(self, page: Any, progress: dict[str, Any], max_results: int) -> int:
        steps = 0
        products = progress.get("products", 0)
        while steps < self.max_scroll_steps and products < max_results:
            page.mouse.wheel(0, self.scroll_step_px)
            page.wait_for_timeout(self.scroll_pause_ms)
            steps += 1
            now = self._eval(page, _SEARCH_PROGRESS_JS)
            if now.get("products", 0) <= products:
                break
            products = now["products"]
        return steps

    # ------------------------------------------------------------------ parse

    def parse(self, raw: RawCapture) -> list[ProductListing]:
        return parser.parse_search_capture(raw)

    # ------------------------------------------------------------------ classify

    def classify_failure(self, exc_or_response: Any) -> ErrorCode | None:
        if isinstance(exc_or_response, QcomError):
            return exc_or_response.code
        status = getattr(exc_or_response, "status", None)
        if isinstance(status, int) and not isinstance(exc_or_response, BaseException):
            if status == 403:
                return ErrorCode.BLOCKED
            if status == 429:
                return ErrorCode.RATE_LIMITED
        return None  # spec section 10: everything else is the generic classifier's

    # ------------------------------------------------------------------ health

    def health_check(self, page: Any) -> HealthReport:
        checks: list[HealthCheck] = []
        expectation = make_expectation(self.probe.pincode, self.probe.city, self.probe.state)
        loc = self.set_location(page, self.probe.pincode, expectation)
        checks.append(HealthCheck(name="header_contains_pincode", ok=loc.effective_pincode == self.probe.pincode, detail=loc.address_text or ""))
        captures = self.search(page, self.probe.term, 20)
        primary = next(c for c in captures if c.parse)
        checks.append(HealthCheck(name="redux_store_present", ok=True, detail=f"{primary.size_bytes} bytes of ui.search.searchProductBffData"))
        checks.append(HealthCheck(name="network_evidence", ok=True, detail=f"{sum(1 for c in captures if c.strategy == STRATEGY_EVIDENCE)} JSON response(s) captured; URLs in the raw store"))
        rows: list[ProductListing] = []
        try:
            rows = self.parse(primary)
        except SchemaDriftError as exc:
            checks.append(HealthCheck(name="documented_paths_present", ok=False, detail=f"SCHEMA_DRIFT at {exc.path}: {exc.message}"))
        except QcomError as exc:
            checks.append(HealthCheck(name="documented_paths_present", ok=False, detail=f"{exc.code.value}: {exc.message}"))
        else:
            checks.append(HealthCheck(name="documented_paths_present", ok=True, detail=f"{len(rows)} product snippet(s), every section 8 key present with its type"))
            checks.append(HealthCheck(name="inventory_is_int", ok=all(isinstance(r.stock_qty, int) for r in rows)))
            merchants = sorted({r.store_or_seller_id or "" for r in rows})
            known = KNOWN_MERCHANT_700048 in merchants
            checks.append(
                HealthCheck(
                    name="known_merchant_present",
                    ok=True,  # a warning, never a failure: stores can legitimately change
                    detail=(f"merchant {KNOWN_MERCHANT_700048} present" if known else f"warning: merchant {KNOWN_MERCHANT_700048} absent; saw {merchants}"),
                )
            )
        return HealthReport(
            platform=self.name,
            adapter_version=self.version,
            ok=all(c.ok for c in checks),
            strategy=STRATEGY_PRIMARY,
            checks=checks,
            location=loc,
            capture_ids=[c.capture_id for c in captures if c.capture_id],
            checked_at_utc=now_utc(),
        )
