from datetime import datetime, timedelta, timezone

from jobbot.models import clean_url, posting_identifier
from jobbot.state import State


def test_state_round_trip(tmp_path, make_state):
    state = State()
    ls = make_state(1)
    state.listings[ls.id] = ls
    path = tmp_path / "data" / "state.json"
    state.save(path)
    loaded = State.load(path)
    assert loaded.listings[ls.id] == ls
    assert State.load(tmp_path / "missing.json") == State()


def test_llm_call_counter_prunes_old_days():
    state = State()
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    state.llm_calls = {"2026-08-01": 40}
    state.record_llm_call(now)
    state.record_llm_call(now)
    assert state.llm_calls == {"2026-09-12": 2}
    assert state.calls_on(now) == 2 and state.calls_on(now + timedelta(days=1)) == 0


def test_refresh_flags_source_updates(make_listing, make_state):
    ls = make_state(1)
    newer = make_listing(1, date_updated=ls.source_updated + timedelta(days=1), active=False)
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    assert ls.refresh_from(newer, now) is True
    assert ls.active is False and ls.last_seen == now
    assert ls.refresh_from(newer, now) is False


def test_same_posting_matches_across_url_formats(make_listing):
    a = make_listing(1, company="Lyft", title="Software Engineer Intern - Backend", url="https://job-boards.greenhouse.io/lyft/jobs/8767726002")
    b = make_listing(2, company="Lyft", title="Software Engineer Intern - Backend", url="https://app.careerpuck.com/job-board/lyft/job/8767726002?gh_jid=8767726002")
    c = make_listing(3, company="Lyft", title="Software Engineer Intern - Backend", url="https://job-boards.greenhouse.io/lyft/jobs/8767726999")
    assert a.dedupe_key == b.dedupe_key != c.dedupe_key
    assert posting_identifier("https://jobs.lever.co/palantir/d5486403-c050-4920-b2e0-91b69b61ebb2/apply") == "d5486403-c050-4920-b2e0-91b69b61ebb2"


def test_clean_url_drops_tracking_but_keeps_ids():
    assert clean_url("https://careers.aqr.com/jobs?gh_jid=7895562&utm_source=Simplify&ref=Simplify") == "https://careers.aqr.com/jobs?gh_jid=7895562"
