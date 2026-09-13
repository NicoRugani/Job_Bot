import json

import httpx
import pytest

from jobbot.notion import API_URL, NotionClient, NotionError, bot_properties, existing_pages_by_listing, page_id_from, sync_listing


class FakeNotion:
    def __init__(self, update_error: NotionError | None = None):
        self.created, self.updated, self.update_error = [], [], update_error

    def create_page(self, data_source_id, properties, children):
        self.created.append((data_source_id, properties, children))
        return {"id": f"page-{len(self.created)}", "url": "https://www.notion.so/page"}

    def update_page(self, page_id, properties):
        if self.update_error:
            raise self.update_error
        self.updated.append((page_id, properties))
        return {}


def test_sync_creates_then_skips_then_updates(make_state):
    notion, ls = FakeNotion(), make_state(1)
    assert sync_listing(notion, "ds-1", ls) is True
    data_source_id, properties, children = notion.created[0]
    assert data_source_id == "ds-1" and properties["Status"] == {"select": {"name": "New"}}
    assert ls.notion_page_id == "page-1" and ls.notion_url and children

    assert sync_listing(notion, "ds-1", ls) is False  # nothing changed

    ls.active = False
    assert sync_listing(notion, "ds-1", ls) is True
    page_id, properties = notion.updated[0]
    assert page_id == "page-1" and properties["Closed"] == {"checkbox": True} and "Status" not in properties


def test_sync_respects_pages_deleted_in_notion(make_state):
    ls = make_state(1, notion_page_id="gone", notion_hash="old")
    assert sync_listing(FakeNotion(NotionError("archived", 400)), "ds", ls) is True
    assert ls.notion_hash != "old"
    with pytest.raises(NotionError):
        sync_listing(FakeNotion(NotionError("unauthorized", 401)), "ds", make_state(2, notion_page_id="p", notion_hash="old"))


def test_properties_shape(make_state):
    props = bot_properties(make_state(1, score=77, tier="good"))
    assert props["Score"] == {"number": 77}
    assert props["Tier"] == {"select": {"name": "✅ Good"}}
    assert props["Role"]["title"][0]["text"]["content"] == "Software Engineer Intern"
    assert props["Posted"] == {"date": {"start": "2026-09-01"}}


def test_client_retries_rate_limits_and_creates_database():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": "db-1", "data_sources": [{"id": "ds-1", "name": "Default"}]})

    client = NotionClient("secret", client=httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert client.create_database("abc", "Internships") == ("db-1", "ds-1")
    last = requests[-1]
    assert last.headers["Notion-Version"] == "2025-09-03" and last.headers["Authorization"] == "Bearer secret"
    body = json.loads(last.content)
    assert body["parent"] == {"type": "page_id", "page_id": "abc"}
    assert body["initial_data_source"]["properties"]["Role"] == {"title": {}}


def test_existing_rows_are_indexed_across_pages():
    responses = [
        {"results": [{"id": "p1", "url": "u1", "properties": {"Listing ID": {"rich_text": [{"plain_text": "simplify:1"}]},
                                                                "Tier": {"select": {"name": "🔥 Strong"}}}}], "has_more": True, "next_cursor": "c2"},
        {"results": [{"id": "p2", "url": "u2", "properties": {"Listing ID": {"rich_text": []}, "Tier": {"select": None}}}], "has_more": False},
    ]
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=responses[len(bodies) - 1])

    client = NotionClient("secret", client=httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    index = existing_pages_by_listing(client, "ds-1")
    assert list(index) == ["simplify:1"] and index["simplify:1"].page_id == "p1" and index["simplify:1"].tier_label == "🔥 Strong"
    assert "start_cursor" not in bodies[0] and bodies[1]["start_cursor"] == "c2"


def test_page_id_from_url():
    assert page_id_from("https://www.notion.so/me/Job-Bot-1f2e3d4c5b6a79881f2e3d4c5b6a7988?pvs=4") == "1f2e3d4c5b6a79881f2e3d4c5b6a7988"
    with pytest.raises(ValueError):
        page_id_from("not a page")
