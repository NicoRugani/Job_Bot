"""Poll specific companies' public job boards directly (Greenhouse, Lever, Ashby).

Aggregators can lag a day or two; watching dream companies directly catches postings sooner.
Only internship titles are kept; the LLM confirms the term.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from ..config import WatchlistSource
from ..models import Listing, clean_url

log = logging.getLogger(__name__)

INTERN = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b", re.IGNORECASE)


def _wrong_term(title: str, term: str) -> bool:
    season, year = term.split()[0].lower(), term.split()[-1]
    other_year = any(y != year for y in re.findall(r"\b20\d\d\b", title))
    other_season = bool(re.search(r"\b(fall|spring|winter|summer|autumn)\b", title, re.I)) and season not in title.lower()
    return other_year or other_season


def _keep(title: str, term: str) -> bool:
    return bool(INTERN.search(title)) and not _wrong_term(title, term)


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def greenhouse(board: str, term: str, client: httpx.Client) -> list[Listing]:
    data = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs").raise_for_status().json()
    return [
        Listing(
            id=f"greenhouse:{board}:{job['id']}",
            source=f"greenhouse:{board}",
            company=job.get("company_name") or board.title(),
            title=job["title"],
            url=clean_url(job["absolute_url"]),
            locations=[job["location"]["name"]] if job.get("location") else [],
            date_posted=_iso(job.get("first_published")),
            date_updated=_iso(job.get("updated_at")),
        )
        for job in data.get("jobs", [])
        if _keep(job["title"], term)
    ]


def lever(company: str, term: str, client: httpx.Client) -> list[Listing]:
    data = client.get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"}).raise_for_status().json()
    return [
        Listing(
            id=f"lever:{company}:{job['id']}",
            source=f"lever:{company}",
            company=company.title(),
            title=job["text"],
            url=clean_url(job["hostedUrl"]),
            locations=[job["categories"]["location"]] if job.get("categories", {}).get("location") else [],
            date_posted=datetime.fromtimestamp(job["createdAt"] / 1000, timezone.utc) if job.get("createdAt") else None,
        )
        for job in data
        if _keep(job["text"], term)
    ]


def ashby(org: str, term: str, client: httpx.Client) -> list[Listing]:
    data = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}").raise_for_status().json()
    return [
        Listing(
            id=f"ashby:{org}:{job['id']}",
            source=f"ashby:{org}",
            company=org.title(),
            title=job["title"],
            url=clean_url(job["jobUrl"]),
            locations=[job["location"]] if job.get("location") else [],
            date_posted=_iso(job.get("publishedAt")),
        )
        for job in data.get("jobs", [])
        if job.get("isListed", True) and _keep(job["title"], term)
    ]


def fetch_watchlist(cfg: WatchlistSource, term: str, client: httpx.Client) -> list[Listing]:
    listings: list[Listing] = []
    for fetch, names in ((greenhouse, cfg.greenhouse), (lever, cfg.lever), (ashby, cfg.ashby)):
        for name in names:
            try:
                listings += fetch(name, term, client)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("watchlist %s/%s failed: %s", fetch.__name__, name, e)
    return listings
