"""Persistent run state (data/state.json), committed back to the repo by the workflow."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from .models import FitAnalysis, Listing, Status, Tier


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class ListingState(BaseModel):
    id: str
    dedupe_key: str
    source: str
    company: str
    title: str
    url: str
    locations: list[str] = []
    other_locations: list[str] = []  # from identical postings at other offices
    area: str | None = None  # "⭐ St. Louis", "US", "Remote", ... (see location.py)
    category: str | None = None
    degrees: list[str] = []
    date_posted: datetime | None = None
    source_updated: datetime | None = None
    first_seen: datetime
    last_seen: datetime
    active: bool = True

    status: Status = "pending"
    filter_reason: str | None = None
    role_group: str | None = None
    role_weight: float | None = None
    prescore: float | None = None
    has_jd: bool | None = None
    jd_source: str | None = None
    jd_hash: str | None = None
    llm_failures: int = 0
    recheck: bool = False  # source says the posting changed; rescore if the JD text differs

    analysis: FitAnalysis | None = None
    final_score: int | None = None
    tier: Tier | None = None
    scored_by: str | None = None
    scored_at: datetime | None = None
    prompt_version: str | None = None  # which scoring instructions produced this score

    notion_page_id: str | None = None
    notion_url: str | None = None
    notion_hash: str | None = None
    notified_at: datetime | None = None

    @classmethod
    def from_listing(cls, listing: Listing, now: datetime) -> ListingState:
        return cls(
            id=listing.id,
            dedupe_key=listing.dedupe_key,
            source=listing.source,
            company=listing.company,
            title=listing.title,
            url=listing.url,
            locations=listing.locations,
            category=listing.category,
            degrees=listing.degrees,
            date_posted=listing.date_posted,
            source_updated=listing.date_updated,
            first_seen=now,
            last_seen=now,
            active=listing.active,
        )

    def refresh_from(self, listing: Listing, now: datetime) -> bool:
        """Sync mutable fields from the latest feed. Returns True if the source says the posting changed."""
        changed = bool(listing.date_updated and self.source_updated and listing.date_updated > self.source_updated)
        self.last_seen = now
        self.active = listing.active
        self.url = listing.url
        self.locations = listing.locations
        self.source_updated = listing.date_updated or self.source_updated
        return changed

    @property
    def tracked(self) -> bool:
        return self.status == "scored" and self.tier is not None


class State(BaseModel):
    version: int = 1
    backfill_complete: bool = False
    llm_calls: dict[str, int] = {}
    listings: dict[str, ListingState] = {}

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=1, exclude_none=True) + "\n")
        os.replace(tmp, path)

    def calls_on(self, now: datetime) -> int:
        return self.llm_calls.get(now.date().isoformat(), 0)

    def record_llm_call(self, now: datetime) -> None:
        day = now.date().isoformat()
        self.llm_calls[day] = self.llm_calls.get(day, 0) + 1
        cutoff = (now - timedelta(days=14)).date().isoformat()
        self.llm_calls = {d: n for d, n in self.llm_calls.items() if d >= cutoff}
