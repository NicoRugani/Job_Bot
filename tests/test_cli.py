from argparse import Namespace

from jobbot.__main__ import cmd_rescore
from jobbot.state import State


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
