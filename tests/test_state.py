from datetime import datetime, timedelta, timezone

from jobbot.models import clean_url, job_key, posting_identifier
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


def test_scores_saved_in_the_old_format_still_load(tmp_path, make_state):
    import json

    state = State()
    ls = make_state(1)
    state.listings[ls.id] = ls
    path = tmp_path / "state.json"
    state.save(path)
    raw = json.loads(path.read_text())
    eligibility = raw["listings"]["simplify:1"]["analysis"]["eligibility"]
    del eligibility["grad_window_earliest"], eligibility["grad_window_latest"]
    eligibility["grad_window_ok"] = "no"  # field from before graduation dates were extracted
    path.write_text(json.dumps(raw))
    assert State.load(path).listings["simplify:1"].analysis.eligibility.grad_window_earliest == ""


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


def test_job_key_matches_postings_across_sites():
    assert job_key("https://job-boards.greenhouse.io/lyft/jobs/8767726002") == job_key(
        "https://app.careerpuck.com/job-board/lyft/job/8767726002?gh_jid=8767726002"
    )
    assert job_key("https://jobs.ashbyhq.com/commure/62841aa1-3ee5-4547-8380-637b737b2cb3") == "62841aa1-3ee5-4547-8380-637b737b2cb3"
    # short ids are only unique within one site
    assert job_key("https://careers-sig.icims.com/jobs/10821/job") != job_key("https://careers-amd.icims.com/jobs/10821/job")
    assert job_key("https://www.example.com/careers/software-intern") is None


def test_clean_url_drops_tracking_but_keeps_ids():
    assert clean_url("https://careers.aqr.com/jobs?gh_jid=7895562&utm_source=Simplify&ref=Simplify") == "https://careers.aqr.com/jobs?gh_jid=7895562"
