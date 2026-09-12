import json

import httpx

from jobbot.notify import Notifier, digest_payload, match_payload


def test_match_payload(make_state):
    ls = make_state(1, score=92, notion_url="https://www.notion.so/page")
    payload = match_payload(ls)
    assert payload["title"] == "92 · Company 1 — Software Engineer Intern"
    assert payload["priority"] == 5
    assert payload["click"] == ls.url
    assert [a["label"] for a in payload["actions"]] == ["Apply", "Notion"]
    assert "$40/hr" in payload["message"]


def test_match_payload_without_notion(make_state):
    payload = match_payload(make_state(1, score=81))
    assert payload["priority"] == 4 and [a["label"] for a in payload["actions"]] == ["Apply"]


def test_digest_truncates(make_state):
    items = [make_state(i) for i in range(20)]
    payload = digest_payload("heading", items, limit=15)
    assert payload["message"].count("\n") == 15 and payload["message"].endswith("5 more in Notion")


def test_notifier_posts_json_with_topic():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})

    Notifier("https://ntfy.sh", "topic-abc", httpx.Client(transport=httpx.MockTransport(handler))).publish({"title": "t", "message": "m"})
    assert str(seen[0].url) == "https://ntfy.sh/"
    assert json.loads(seen[0].content) == {"topic": "topic-abc", "title": "t", "message": "m"}
