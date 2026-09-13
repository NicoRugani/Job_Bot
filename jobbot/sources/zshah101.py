"""Automated internship list that polls ~4,800 employer job boards every 30 minutes
(github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships), published as CSV."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

import httpx

from ..models import Listing, clean_url

OTHER_SEASONS = re.compile(r"\b(fall|spring|winter|autumn)\b", re.IGNORECASE)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_zshah101(text: str, term: str) -> list[Listing]:
    year = term.split()[-1]
    listings = []
    for row in csv.DictReader(io.StringIO(text)):
        title, url, season = (row.get("title") or "").strip(), (row.get("url") or "").strip(), (row.get("season") or "").strip()
        if not title or not url:
            continue
        if season != term:
            # "Not stated" rows are kept unless the title names another season or year; the LLM confirms the term.
            other_year = any(y != year for y in re.findall(r"\b20\d\d\b", title))
            if season not in ("", "Not stated") or OTHER_SEASONS.search(title) or other_year:
                continue
        locations = [row["location"].strip()] if (row.get("location") or "").strip() else []
        if (row.get("remote") or "").strip().lower() == "yes":
            locations.append("Remote")
        listings.append(
            Listing(
                id=f"zshah101:{row.get('id') or url}",
                source="zshah101",
                company=(row.get("company") or "").strip(),
                title=title,
                url=clean_url(url),
                locations=locations,
                category=row.get("category") or None,
                terms=[term] if season == term else [],
                date_posted=_time(row.get("posted_at")),
            )
        )
    return listings


def fetch_zshah101(url: str, term: str, client: httpx.Client) -> list[Listing]:
    response = client.get(url, timeout=120)
    response.raise_for_status()
    return parse_zshah101(response.text, term)
