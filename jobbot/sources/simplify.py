"""SimplifyJobs internship list (github.com/SimplifyJobs/Summer2027-Internships), refreshed daily."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Listing, clean_url

OTHER_SEASONS = re.compile(r"\b(fall|spring|winter|autumn)\b", re.IGNORECASE)


def _timestamp(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) and value > 0 else None


def parse_simplify(raw: list[dict[str, Any]], term: str) -> list[Listing]:
    year = term.split()[-1]
    listings = []
    for item in raw:
        if not item.get("is_visible", True) or not item.get("url"):
            continue
        terms = item.get("terms") or []
        title = item.get("title", "")
        if term not in terms:
            # Some postings carry no term; accept those only if the title names the year and no other season.
            unlabeled = all(t in ("N/A", "") for t in terms)
            if not (unlabeled and year in title and not OTHER_SEASONS.search(title)):
                continue
        listings.append(
            Listing(
                id=f"simplify:{item['id']}",
                source="simplify",
                company=item.get("company_name", "").strip(),
                title=title.strip(),
                url=clean_url(item["url"]),
                locations=item.get("locations") or [],
                category=item.get("category"),
                terms=terms,
                degrees=item.get("degrees") or [],
                date_posted=_timestamp(item.get("date_posted")),
                date_updated=_timestamp(item.get("date_updated")),
                active=bool(item.get("active")),
            )
        )
    return listings


def fetch_simplify(url: str, term: str, client: httpx.Client) -> list[Listing]:
    response = client.get(url, timeout=120)
    response.raise_for_status()
    return parse_simplify(response.json(), term)
