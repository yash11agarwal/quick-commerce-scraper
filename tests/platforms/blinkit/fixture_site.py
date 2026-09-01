"""A local stand-in for blinkit.com, served through a Playwright route so the adapter drives a
real Chromium against the real URLs without any network.

It models only what the spec describes: a header with `Select Location` or `Delivery in N
minutes` plus the address, a picker with the `search delivery location` input, an autocomplete
list that includes the Madhya Pradesh decoy, a reload on selection, a header search input, the
``/s/?q=`` route, a Redux store at ``window.__reduxStore__`` that grows on scroll, and one JSON
XHR during search. It proves the adapter's driving logic, not the live site's markup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "blinkit"

DEFAULT_SUGGESTIONS = [
    ["Purani Basti", "Patehra, Maihar, Madhya Pradesh 700048, India"],  # the decoy the playbooks warn about
    ["Patipukur", "Kolkata, West Bengal 700048, India"],
    ["Dakshindari", "South Dumdum, West Bengal 700048, India"],
]

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Blinkit fixture</title>
<style>main{min-height:6000px} .sugg{cursor:pointer;padding:4px} [hidden]{display:none}</style>
</head>
<body>
<header id="hdr">
  <div id="loc"><div id="loc-label"></div><div id="loc-addr"></div></div>
  <div id="search-bar"></div>
</header>
<div id="modal" hidden>
  <input id="loc-input" placeholder="search delivery location">
  <div id="suggestions"></div>
</div>
<main id="results"></main>
<script>
const CFG = __CFG__;
const saved = localStorage.getItem("qcom_fixture_loc");
const label = document.getElementById("loc-label"), addr = document.getElementById("loc-addr");
if (!saved) { label.textContent = "Select Location"; }
else {
  label.textContent = CFG.header_mode === "unavailable" ? "Currently unavailable" : "Delivery in 20 minutes";
  let a = saved;
  if (CFG.header_mode === "wrong_state") a = "Purani Basti, Patehra, Maihar, Madhya Pradesh 700048, India";
  if (CFG.header_mode === "no_pincode") a = "Patipukur, Kolkata, West Bengal, India";
  addr.textContent = a;
}
document.getElementById("loc").addEventListener("click", () => { document.getElementById("modal").hidden = false; });
const input = document.getElementById("loc-input");
input.addEventListener("input", () => {
  const v = input.value;
  setTimeout(() => {
    const box = document.getElementById("suggestions");
    box.innerHTML = "";
    CFG.suggestions.filter(s => (s[0] + " " + s[1]).includes(v)).forEach(s => {
      const d = document.createElement("div"); d.className = "sugg"; d.setAttribute("role", "button");
      const a = document.createElement("span"); a.textContent = s[0];
      const b = document.createElement("span"); b.textContent = s[1];
      d.appendChild(a); d.appendChild(b);
      d.addEventListener("click", () => { localStorage.setItem("qcom_fixture_loc", d.innerText); location.reload(); });
      box.appendChild(d);
    });
  }, 150);
});
if (CFG.search_input) {
  const s = document.createElement("input"); s.placeholder = "Search for atta dal and more";
  s.addEventListener("keydown", e => { if (e.key === "Enter") location.href = "/s/?q=" + encodeURIComponent(s.value); });
  document.getElementById("search-bar").appendChild(s);
}
if (location.pathname.startsWith("/s/")) {
  const q = new URLSearchParams(location.search).get("q") || "";
  document.getElementById("results").textContent = "results for " + q;
  fetch("/fixture/search.json?q=" + encodeURIComponent(q)).then(r => r.json()).then(() => {});
  if (CFG.redux) {
    const all = CFG.snippets;
    let shown = Math.min(CFG.first_batch, all.length);
    const state = {
      ui: { search: { searchProductBffData: CFG.slice_missing ? undefined : { snippets: all.slice(0, shown) } } },
      data: { location: { _fixture: saved }, merchant: null, eta: null, addressesV2: null, chainId: 1 },
    };
    window.__reduxStore__ = { getState: () => state };
    const grow = () => { if (shown < all.length && !CFG.slice_missing) { shown = Math.min(shown + CFG.first_batch, all.length); state.ui.search.searchProductBffData = { snippets: all.slice(0, shown) }; } };
    window.addEventListener("wheel", grow); window.addEventListener("scroll", grow);
  }
}
</script>
</body></html>
"""


def default_config(**overrides: Any) -> dict[str, Any]:
    snippets = json.loads((FIXTURES / "normal.json").read_text(encoding="utf-8"))["searchProductBffData"]["snippets"]
    cfg: dict[str, Any] = {
        "suggestions": DEFAULT_SUGGESTIONS,
        "header_mode": "eta",  # eta | unavailable | wrong_state | no_pincode
        "snippets": snippets,
        "first_batch": 3,  # products appear three at a time, one batch per scroll
        "search_input": True,
        "doc_status": 200,
        "redux": True,
        "slice_missing": False,
    }
    cfg.update(overrides)
    return cfg


def site_html(cfg: dict[str, Any]) -> str:
    return _HTML.replace("__CFG__", json.dumps(cfg))


def install(context: Any, cfg: dict[str, Any], hits: list[str] | None = None) -> None:
    """Route every blinkit.com request in ``context`` to the fixture site."""
    html = site_html(cfg)

    def handler(route: Any) -> None:
        url = route.request.url
        if hits is not None:
            hits.append(url)
        if "/fixture/search.json" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"fixture": True, "url": url}))
            return
        route.fulfill(status=cfg["doc_status"], content_type="text/html; charset=utf-8", body=html)

    context.route("https://blinkit.com/**", handler)
