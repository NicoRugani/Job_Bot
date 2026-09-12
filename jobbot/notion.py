"""Minimal Notion API client for the tracking database (Notion-Version 2025-09-03: databases contain data sources)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from .state import ListingState

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

TIER_LABEL = {"strong": "🔥 Strong", "good": "✅ Good", "possible": "🤔 Possible"}
ROLE_LABEL = {"swe": "SWE", "embedded": "Embedded/HW", "ux": "UX/UI", "other": "Other"}
MODE_LABEL = {"onsite": "In person", "hybrid": "Hybrid", "remote": "Remote", "unknown": "Unknown"}
STATUS_OPTIONS = ["New", "Interested", "Applied", "OA", "Interview", "Offer", "Rejected", "Pass"]


def _select_schema(*names_and_colors: tuple[str, str]) -> dict:
    return {"select": {"options": [{"name": name, "color": color} for name, color in names_and_colors]}}


DATABASE_PROPERTIES: dict[str, dict] = {
    "Role": {"title": {}},
    "Company": {"rich_text": {}},
    "Score": {"number": {"format": "number"}},
    "Tier": _select_schema(("🔥 Strong", "red"), ("✅ Good", "green"), ("🤔 Possible", "yellow")),
    "Status": _select_schema(
        ("New", "blue"), ("Interested", "purple"), ("Applied", "orange"), ("OA", "yellow"),
        ("Interview", "pink"), ("Offer", "green"), ("Rejected", "gray"), ("Pass", "brown"),
    ),
    "Role type": _select_schema(("SWE", "blue"), ("UX/UI", "pink"), ("Embedded/HW", "orange"), ("Other", "gray")),
    "Work mode": _select_schema(("In person", "blue"), ("Hybrid", "purple"), ("Remote", "green"), ("Unknown", "gray")),
    "Paid": _select_schema(("Paid", "green"), ("Unpaid", "red"), ("Unknown", "gray")),
    "Pay": {"rich_text": {}},
    "Relocation": _select_schema(("Yes", "green"), ("No", "red"), ("Unknown", "gray")),
    "Location": {"rich_text": {}},
    "Why it fits": {"rich_text": {}},
    "Gaps / red flags": {"rich_text": {}},
    "Apply link": {"url": {}},
    "Posted": {"date": {}},
    "Found": {"date": {}},
    "Confidence": _select_schema(("High", "green"), ("Medium", "yellow"), ("Low", "red")),
    "JD read": {"checkbox": {}},
    "Closed": {"checkbox": {}},
    "Scored by": {"rich_text": {}},
    "Notes": {"rich_text": {}},
    "Listing ID": {"rich_text": {}},
}


class NotionError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def page_id_from(value: str) -> str:
    """Accept a raw id or a Notion page URL."""
    match = re.findall(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value.lower())
    if not match:
        raise ValueError(f"no Notion page id found in {value!r}")
    return match[-1]


class NotionClient:
    def __init__(
        self,
        token: str,
        requests_per_second: float = 2.5,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client or httpx.Client(base_url=API_URL, timeout=30)
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
        self.min_interval = 1.0 / requests_per_second
        self.sleep = sleep
        self.clock = clock
        self._last: float | None = None

    def request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        for attempt in range(5):
            if self._last is not None and (wait := self.min_interval - (self.clock() - self._last)) > 0:
                self.sleep(wait)
            self._last = self.clock()
            response = self.client.request(method, path, json=body, headers=self.headers)
            if response.status_code == 429 or response.status_code >= 500:
                self.sleep(float(response.headers.get("Retry-After", 2**attempt)))
                continue
            if response.is_error:
                raise NotionError(f"{method} {path} -> {response.status_code}: {response.text[:500]}", response.status_code)
            return response.json()
        raise NotionError(f"{method} {path} failed after retries")

    def create_database(self, parent_page_id: str, title: str) -> tuple[str, str]:
        data = self.request(
            "POST",
            "/databases",
            {
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "title": [{"type": "text", "text": {"content": title}}],
                "is_inline": False,
                "initial_data_source": {"properties": DATABASE_PROPERTIES},
            },
        )
        return data["id"], data["data_sources"][0]["id"]

    def create_page(self, data_source_id: str, properties: dict, children: list[dict]) -> dict[str, Any]:
        body = {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": properties}
        if children:
            body["children"] = children
        return self.request("POST", "/pages", body)

    def update_page(self, page_id: str, properties: dict) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})


def _text(value: str) -> list[dict]:
    return [{"type": "text", "text": {"content": value[:1900]}}] if value else []


def _select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def bot_properties(ls: ListingState) -> dict[str, Any]:
    """Properties the bot owns. Status and Notes are left alone after creation."""
    a = ls.analysis
    assert a is not None
    return {
        "Role": {"title": _text(ls.title)},
        "Company": {"rich_text": _text(ls.company)},
        "Score": {"number": ls.final_score},
        "Tier": _select(TIER_LABEL.get(ls.tier or "")),
        "Role type": _select(ROLE_LABEL[a.role_type]),
        "Work mode": _select(MODE_LABEL[a.work_mode]),
        "Paid": _select(a.paid.capitalize()),
        "Pay": {"rich_text": _text(a.pay_text)},
        "Relocation": _select(a.relocation_assistance.capitalize()),
        "Location": {"rich_text": _text("; ".join(dict.fromkeys(ls.locations + ls.other_locations)))},
        "Why it fits": {"rich_text": _text(a.summary)},
        "Gaps / red flags": {"rich_text": _text("; ".join(a.red_flags + a.gaps))},
        "Apply link": {"url": ls.url},
        "Posted": {"date": {"start": ls.date_posted.date().isoformat()} if ls.date_posted else None},
        "Found": {"date": {"start": ls.first_seen.date().isoformat()}},
        "Confidence": _select(a.confidence.capitalize()),
        "JD read": {"checkbox": bool(ls.has_jd)},
        "Closed": {"checkbox": not ls.active},
        "Scored by": {"rich_text": _text(ls.scored_by or "")},
        "Listing ID": {"rich_text": _text(ls.id)},
    }


def page_children(ls: ListingState) -> list[dict]:
    a = ls.analysis
    assert a is not None

    def heading(text: str) -> dict:
        return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _text(text)}}

    def bullets(items: list[str]) -> list[dict]:
        return [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _text(i)}} for i in items]

    blocks = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(a.summary)}},
        heading("Eligibility"),
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _text(a.eligibility.notes)}},
    ]
    for title, items in (("Matched skills", a.matched_skills), ("Gaps", a.gaps), ("Red flags", a.red_flags)):
        if items:
            blocks += [heading(title), *bullets(items)]
    return blocks


def properties_hash(properties: dict) -> str:
    return hashlib.sha256(json.dumps(properties, sort_keys=True).encode()).hexdigest()[:16]


def sync_listing(notion: NotionClient, data_source_id: str, ls: ListingState) -> bool:
    """Create or update the listing's page. Returns True if Notion was written."""
    properties = bot_properties(ls)
    digest = properties_hash(properties)
    if ls.notion_page_id:
        if digest == ls.notion_hash:
            return False
        try:
            notion.update_page(ls.notion_page_id, properties)
        except NotionError as e:
            if e.status not in (400, 404):
                raise
            # Page was deleted/archived in Notion: respect that and stop syncing it.
        ls.notion_hash = digest
        return True
    page = notion.create_page(data_source_id, {**properties, "Status": _select("New")}, page_children(ls))
    ls.notion_page_id = page["id"]
    ls.notion_url = page.get("url")
    ls.notion_hash = digest
    return True
