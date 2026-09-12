from argparse import Namespace

import pytest

from jobbot.__main__ import cmd_rescore, cmd_run
from jobbot.state import State


def test_run_stops_before_any_work_when_secrets_are_missing(settings, monkeypatch, capsys):
    settings.secrets.gemini_api_key = None
    settings.secrets.ntfy_topic = None
    monkeypatch.setattr("jobbot.__main__.run", lambda *args, **kwargs: pytest.fail("pipeline should not start"))
    assert cmd_run(settings, Namespace(dry_run=False, limit=None, no_notify=False)) == 2
    error = capsys.readouterr().err
    assert "GEMINI_API_KEY" in error and "NTFY_TOPIC" in error and "NOTION_TOKEN" not in error


def test_paused_run_is_flagged_in_github_actions(settings, monkeypatch, capsys):
    from jobbot.pipeline import RunReport

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr("jobbot.__main__.run", lambda *args, **kwargs: RunReport(pending=618, quota_paused=True, pause_reason="503 UNAVAILABLE"))
    assert cmd_run(settings, Namespace(dry_run=False, limit=None, no_notify=False)) == 0
    assert "::warning title=Gemini paused::Scored 0, 618 still pending. 503 UNAVAILABLE" in capsys.readouterr().out


def test_dry_run_only_needs_gemini(settings, monkeypatch):
    settings.secrets.notion_token = settings.secrets.ntfy_topic = None
    monkeypatch.setattr("jobbot.__main__.run", lambda *args, **kwargs: pytest.fail("reached pipeline"))
    with pytest.raises(pytest.fail.Exception, match="reached pipeline"):
        cmd_run(settings, Namespace(dry_run=True, limit=None, no_notify=False))


def test_rescore_unknown_id_writes_nothing(settings):
    assert cmd_rescore(settings, Namespace(ids=["simplify:nope"])) == 1
    assert not settings.state_path.exists()


def test_rescore_queues_known_listing(settings, make_state):
    state = State()
    ls = make_state(1)
    state.listings[ls.id] = ls
    state.save(settings.state_path)

    assert cmd_rescore(settings, Namespace(ids=["all"])) == 0
    reloaded = State.load(settings.state_path).listings[ls.id]
    assert reloaded.status == "pending" and reloaded.analysis is not None  # old analysis kept until replaced
