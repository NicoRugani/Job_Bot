"""Push notifications via ntfy's JSON publishing API (https://docs.ntfy.sh/publish/#publish-as-json)."""

from __future__ import annotations

import httpx

from .state import ListingState

MODE_LABEL = {"onsite": "In person", "hybrid": "Hybrid", "remote": "Remote", "unknown": "Work mode n/a"}


class Notifier:
    def __init__(self, server: str, topic: str, client: httpx.Client | None = None):
        self.url = server.rstrip("/") + "/"
        self.topic = topic
        self.client = client or httpx.Client(timeout=20)

    def publish(self, payload: dict) -> None:
        response = self.client.post(self.url, json={"topic": self.topic, **payload})
        response.raise_for_status()


def match_payload(ls: ListingState) -> dict:
    a = ls.analysis
    assert a is not None and ls.final_score is not None
    pay = a.pay_text or {"paid": "Paid (amount not listed)", "unpaid": "Unpaid", "unknown": "Pay not listed"}[a.paid]
    lines = [
        a.summary,
        "",
        f"📍 {'; '.join(dict.fromkeys(ls.locations + ls.other_locations)) or 'Location n/a'} · {MODE_LABEL[a.work_mode]}",
        f"💵 {pay}",
        f"🚚 Relocation: {a.relocation_assistance}",
    ]
    if a.red_flags:
        lines.append("⚠️ " + "; ".join(a.red_flags[:3]))
    actions = [{"action": "view", "label": "Apply", "url": ls.url}]
    if ls.notion_url:
        actions.append({"action": "view", "label": "Notion", "url": ls.notion_url})
    return {
        "title": f"{ls.final_score} · {ls.company} — {ls.title}",
        "message": "\n".join(lines),
        "priority": 5 if ls.final_score >= 90 else 4,
        "tags": ["fire"],
        "click": ls.url,
        "actions": actions,
    }


def digest_payload(heading: str, items: list[ListingState], limit: int = 15) -> dict:
    lines = [f"{ls.final_score} · {ls.company} — {ls.title}" for ls in items[:limit]]
    if len(items) > limit:
        lines.append(f"…and {len(items) - limit} more in Notion")
    return {"title": heading, "message": "\n".join(lines), "priority": 4, "tags": ["clipboard"]}
