"""Minimal Notion API client for the tracking database (Notion-Version 2025-09-03: databases contain data sources)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .models import normalize_text
from .state import ListingState

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
VIEWS_API_VERSION = "2026-03-11"  # the /views endpoints need this newer version

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
    "Applied on": {"date": {}},
    "Area": _select_schema(("⭐ St. Louis", "orange"), ("⭐ Bozeman", "orange"), ("US", "blue"), ("Remote", "green"), ("Unknown", "gray")),
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
    def __init__(self, message: str, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient  # timeouts, outages: worth retrying on a later run


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
        self.client = client or httpx.Client(base_url=API_URL, timeout=45)
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
        self.min_interval = 1.0 / requests_per_second
        self.sleep = sleep
        self.clock = clock
        self._last: float | None = None

    def request(
        self, method: str, path: str, body: dict | None = None, *, params: dict | None = None, version: str = NOTION_VERSION
    ) -> dict[str, Any]:
        headers = {**self.headers, "Notion-Version": version}
        # Reads and updates are safe to repeat after a network error. A create that timed out may have gone
        # through, so it isn't repeated; the next run finds that row by its Listing ID instead of duplicating it.
        repeatable = method in ("GET", "PATCH", "DELETE") or path.endswith("/query")
        for attempt in range(5):
            if self._last is not None and (wait := self.min_interval - (self.clock() - self._last)) > 0:
                self.sleep(wait)
            self._last = self.clock()
            try:
                response = self.client.request(method, path, json=body, params=params, headers=headers)
            except httpx.TransportError as e:
                if not repeatable or attempt >= 2:
                    raise NotionError(f"{method} {path} failed: {e!r}", transient=True) from e
                self.sleep(5.0 * (attempt + 1))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                try:
                    delay = float(response.headers.get("Retry-After", 2**attempt))
                except ValueError:
                    delay = float(2**attempt)
                self.sleep(delay)
                continue
            if response.is_error:
                raise NotionError(f"{method} {path} -> {response.status_code}: {response.text[:500]}", response.status_code)
            return response.json()
        raise NotionError(f"{method} {path} failed after retries", transient=True)

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

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self.request("GET", f"/pages/{page_id}")

    def trash_page(self, page_id: str) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", {"in_trash": True})

    def data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.request("GET", f"/data_sources/{data_source_id}")

    def list_views(self, database_id: str) -> list[dict[str, Any]]:
        """Full view objects (the list endpoint only returns ids)."""
        views: list[dict[str, Any]] = []
        cursor = None
        while True:
            params = {"database_id": database_id, "page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            data = self.request("GET", "/views", params=params, version=VIEWS_API_VERSION)
            views += [self.request("GET", f"/views/{ref['id']}", version=VIEWS_API_VERSION) for ref in data.get("results", [])]
            if not data.get("has_more"):
                return views
            cursor = data.get("next_cursor")

    def create_view(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/views", body, version=VIEWS_API_VERSION)

    def update_view(self, view_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/views/{view_id}", body, version=VIEWS_API_VERSION)

    def ensure_properties(self, data_source_id: str, properties: dict[str, dict]) -> list[str]:
        """Add columns the database is missing (e.g. ones introduced after it was created). Returns their names."""
        existing = self.data_source(data_source_id).get("properties", {})
        missing = {name: config for name, config in properties.items() if name not in existing}
        if missing:
            self.request("PATCH", f"/data_sources/{data_source_id}", {"properties": missing})
        return list(missing)

    def query_data_source(self, data_source_id: str) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self.request("POST", f"/data_sources/{data_source_id}/query", body)
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                return pages
            cursor = data.get("next_cursor")


@dataclass
class ExistingPage:
    page_id: str
    url: str | None
    tier_label: str | None
    status: str | None = None


def row_name_key(company: str, role: str) -> str:
    return f"name:{normalize_text(company)}|{normalize_text(role)}"


LIST_COLUMNS = ["Role", "Company", "Score", "Tier", "Area", "Status", "Work mode", "Pay", "Apply link", "Posted"]
DEFAULT_VIEW_NAMES = {"Default view", "Table", "Untitled"}


def view_specs(home_areas: list[str]) -> list[dict[str, Any]]:
    """The views set up in the tracking database. Filters and sorts use the data source query shapes."""
    still_open = {"property": "Closed", "checkbox": {"equals": False}}
    by_score = [{"property": "Score", "direction": "descending"}]
    specs: list[dict[str, Any]] = [
        {"name": "To review", "type": "table", "sorts": by_score, "show": LIST_COLUMNS,
         "filter": {"and": [{"property": "Status", "select": {"equals": "New"}}, still_open]}},
        {"name": "Application board", "type": "board", "group_by": "Status", "sorts": by_score,
         "show": ["Role", "Company", "Score", "Area", "Applied on"], "filter": still_open},
        {"name": "Strong matches", "type": "table", "sorts": by_score, "show": LIST_COLUMNS,
         "filter": {"and": [{"property": "Tier", "select": {"equals": TIER_LABEL["strong"]}}, still_open]}},
    ]
    if home_areas:
        near = [{"property": "Area", "select": {"equals": area}} for area in home_areas]
        specs.append({"name": "Near home", "type": "table", "sorts": by_score, "show": LIST_COLUMNS,
                      "filter": {"and": [near[0] if len(near) == 1 else {"or": near}, still_open]}})
    specs.append(
        {"name": "Applied", "type": "table", "sorts": [{"property": "Applied on", "direction": "descending"}],
         "show": ["Role", "Company", "Status", "Applied on", "Notes", "Apply link", "Score", "Area"],
         "filter": {"or": [{"property": "Status", "select": {"equals": s}} for s in ("Applied", "OA", "Interview", "Offer")]}}
    )
    return specs


ALL_MATCHES_VIEW: dict[str, Any] = {
    "name": "All matches", "type": "table", "sorts": [{"property": "Score", "direction": "descending"}],
    "show": LIST_COLUMNS + ["Relocation", "Location", "Why it fits", "Gaps / red flags", "Applied on", "Notes", "Found"],
}


def _view_body(spec: dict[str, Any], property_ids: dict[str, str]) -> dict[str, Any]:
    shown = [name for name in spec["show"] if name in property_ids]
    columns = [{"property_id": property_ids[name], "visible": True} for name in shown]
    columns += [{"property_id": pid, "visible": False} for name, pid in property_ids.items() if name not in shown]
    configuration: dict[str, Any] = {"type": spec["type"], "properties": columns}
    if spec["type"] == "board":
        configuration["group_by"] = {"type": "select", "property_id": property_ids[spec["group_by"]], "sort": {"type": "manual"}, "hide_empty_groups": False}
    body: dict[str, Any] = {"name": spec["name"], "sorts": spec["sorts"], "configuration": configuration}
    if spec.get("filter"):
        body["filter"] = spec["filter"]
    return body


def setup_views(notion: NotionClient, data_source_id: str, home_areas: list[str]) -> list[str]:
    """Create the tracking views that don't exist yet (matched by name) and tidy the untouched default view."""
    source = notion.data_source(data_source_id)
    database_id = (source.get("parent") or {}).get("database_id")
    property_ids = {name: prop["id"] for name, prop in (source.get("properties") or {}).items()}
    existing = notion.list_views(database_id)
    names = {view.get("name") for view in existing}
    changes = []
    for spec in view_specs(home_areas):
        if spec["name"] in names:
            continue
        notion.create_view({"database_id": database_id, "data_source_id": data_source_id, "type": spec["type"], **_view_body(spec, property_ids)})
        changes.append(f"created view '{spec['name']}'")
    default = next((v for v in existing if v.get("name") in DEFAULT_VIEW_NAMES and v.get("type") == "table"), None)
    if default is not None and ALL_MATCHES_VIEW["name"] not in names:
        notion.update_view(default["id"], _view_body(ALL_MATCHES_VIEW, property_ids))
        changes.append(f"turned '{default.get('name')}' into '{ALL_MATCHES_VIEW['name']}'")
    return changes


def page_untouched(page: dict[str, Any]) -> bool:
    """True if you haven't used the row yet: Status still New, no notes, no applied date."""
    props = page.get("properties") or {}
    status = ((props.get("Status") or {}).get("select") or {}).get("name")
    notes = "".join(t.get("plain_text", "") for t in (props.get("Notes") or {}).get("rich_text", []))
    applied = (props.get("Applied on") or {}).get("date")
    return status in (None, "New") and not notes and not applied


def existing_pages_by_listing(notion: NotionClient, data_source_id: str) -> dict[str, ExistingPage]:
    """Rows already in the database, keyed by Listing ID; rows added by hand (no Listing ID) by company + role."""
    index: dict[str, ExistingPage] = {}
    for page in notion.query_data_source(data_source_id):
        props = page.get("properties") or {}

        def plain(name: str, kind: str = "rich_text") -> str:
            return "".join(t.get("plain_text", "") for t in (props.get(name) or {}).get(kind) or [])

        row = ExistingPage(
            page["id"],
            page.get("url"),
            ((props.get("Tier") or {}).get("select") or {}).get("name"),
            ((props.get("Status") or {}).get("select") or {}).get("name"),
        )
        if listing_id := plain("Listing ID"):
            index[listing_id] = row
        elif (company := plain("Company")) and (role := plain("Role", "title")):
            index.setdefault(row_name_key(company, role), row)
    return index


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
        "Area": _select(ls.area),
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
