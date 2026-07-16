"""Parse LinkedIn guest-endpoint HTML fragments into JobRecords.

The search endpoint returns a flat list of ``<li>`` job cards. The
selectors below match LinkedIn's guest markup (class names like
``base-search-card__title``); they're stable but not contractual, so every
extraction degrades gracefully — a card missing a field yields None for
that field rather than aborting the page.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from .schema import JobRecord, canonical_job_url

log = logging.getLogger(__name__)

#: data-entity-urn="urn:li:jobPosting:4012345678"
_URN_RE = re.compile(r"urn:li:jobPosting:(\d+)")
#: fallback: id embedded in the card link, e.g. ...-4012345678?position=1
_LINK_ID_RE = re.compile(r"-(\d{6,})(?:\?|$)")


def _text(node: Optional[Tag]) -> Optional[str]:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def _extract_job_id(card: Tag) -> Optional[str]:
    for attr in ("data-entity-urn", "data-tracking-control-name"):
        value = card.get(attr) or ""
        m = _URN_RE.search(value)
        if m:
            return m.group(1)
    link = card.select_one("a.base-card__full-link[href]")
    if link:
        m = _LINK_ID_RE.search(link["href"].split("?")[0] + "?")
        if m:
            return m.group(1)
    return None


def parse_search_page(html: str) -> list[JobRecord]:
    """All job cards on one guest search-results page."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.base-card") or soup.select("li > div")
    records: list[JobRecord] = []
    for card in cards:
        job_id = _extract_job_id(card)
        title = _text(card.select_one(".base-search-card__title"))
        if not job_id or not title:
            log.debug("skipping card without id/title: %s",
                      str(card)[:200])
            continue
        # Posted date: LinkedIn uses listdate or listdate--new; both carry
        # a machine-readable datetime attribute.
        time_tag = card.select_one("time[datetime]")
        records.append(JobRecord(
            job_id=job_id,
            title=title,
            company=_text(card.select_one(".base-search-card__subtitle")),
            location=_text(card.select_one(".job-search-card__location")),
            url=canonical_job_url(job_id),
            posted_date=time_tag["datetime"] if time_tag else None,
        ))
    return records


def parse_job_description(html: str) -> Optional[str]:
    """Plain-text description from a posting's guest detail page."""
    soup = BeautifulSoup(html, "html.parser")
    node = (soup.select_one(".show-more-less-html__markup")
            or soup.select_one(".description__text"))
    if node is None:
        return None
    # Keep paragraph/list structure readable in a terminal.
    for br in node.find_all("br"):
        br.replace_with("\n")
    for li in node.find_all("li"):
        li.insert_before("\n- ")
    text = node.get_text()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None
