"""Poll the public job boards (Greenhouse, Lever, Ashby) of every company that has shown up in any list.

The lists lag the employer by hours to days and miss some postings entirely; the company's own
board has them first. Boards are discovered from listing URLs, so no configuration is needed.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..models import Listing
from .ats_watchlist import ashby, greenhouse, lever

log = logging.getLogger(__name__)

BOARD_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?!embed/)([\w-]+)/jobs/\d+")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([^/?#]+)/[0-9a-f-]{36}", re.IGNORECASE)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/[0-9a-f-]{36}", re.IGNORECASE)),
]
FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def discover_boards(postings: Iterable[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(kind, board, company) for every board seen in (url, company) pairs, most-seen first."""
    counts: Counter[tuple[str, str]] = Counter()
    names: dict[tuple[str, str], str] = {}
    slugs: dict[tuple[str, str], str] = {}
    for url, company in postings:
        for kind, pattern in BOARD_PATTERNS:
            if match := pattern.search(url):
                key = (kind, match.group(1).lower())
                counts[key] += 1
                slugs.setdefault(key, match.group(1))
                names.setdefault(key, company)
                break
    return [(kind, slugs[(kind, board)], names[(kind, board)]) for (kind, board), _ in counts.most_common()]


def fetch_company_boards(
    boards: list[tuple[str, str, str]], term: str, client: httpx.Client, concurrency: int = 8
) -> tuple[list[Listing], set[str]]:
    """Internship postings from each board, plus the sources that were checked successfully."""

    def poll(board: tuple[str, str, str]) -> tuple[tuple[str, str, str], list[Listing] | None]:
        kind, slug, _ = board
        try:
            return board, FETCHERS[kind](slug, term, client)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            log.debug("board %s/%s failed: %s", kind, slug, e)
            return board, None

    listings: list[Listing] = []
    checked: set[str] = set()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for (kind, slug, company), found in pool.map(poll, boards):
            if found is None:
                continue
            checked.add(f"{kind}:{slug}")
            # Board APIs often lack a readable company name; use the one the lists gave.
            listings += [listing.model_copy(update={"company": company}) for listing in found]
    log.info("Company boards: polled %d/%d, %d internship postings", len(checked), len(boards), len(listings))
    return listings, checked
