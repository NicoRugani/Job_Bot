"""Curated US software internship table (github.com/speedyapply/2027-SWE-College-Jobs), updated daily."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

import httpx

from ..models import Listing, clean_url, posting_identifier

OTHER_SEASONS = re.compile(r"\b(fall|spring|winter|autumn|january|february)\b", re.IGNORECASE)


def _plain(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>|\*\*", " ", cell)).strip()


def parse_speedyapply(markdown: str, term: str, now: datetime | None = None) -> list[Listing]:
    now = now or datetime.now(timezone.utc)
    year = term.split()[-1]
    headers: list[str] = []
    listings = []
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0].lower() == "company":
            headers = [cell.lower() for cell in cells]
            continue
        if not headers or len(cells) != len(headers) or all(set(cell) <= set("-: ") for cell in cells):
            continue
        row = dict(zip(headers, cells))
        links = re.findall(r'href="(https?://[^"]+)"', row.get("posting", ""))
        company, title = _plain(row.get("company", "")), _plain(row.get("position", ""))
        other_year = any(y != year for y in re.findall(r"\b20\d\d\b", title))
        if not (links and company and title) or OTHER_SEASONS.search(title) or other_year:
            continue
        age = re.match(r"(\d+)\s*d", row.get("age", ""))
        posted = (now - timedelta(days=int(age.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0) if age else None
        url = clean_url(links[-1])
        locations = [part.strip() for part in re.split(r"<br\s*/?>|;", row.get("location", "")) if _plain(part)]
        listings.append(
            Listing(
                id=f"speedyapply:{posting_identifier(url)}",
                source="speedyapply",
                company=company,
                title=title,
                url=url,
                locations=[_plain(part) for part in locations],
                date_posted=posted,
            )
        )
    return listings


def fetch_speedyapply(url: str, term: str, client: httpx.Client) -> list[Listing]:
    response = client.get(url, timeout=60)
    response.raise_for_status()
    return parse_speedyapply(response.text, term)
