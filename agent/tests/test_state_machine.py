"""State machine + repository tests (SPEC §4), all in-memory (no network)."""

from __future__ import annotations

from itertools import product

import pytest

from proofpay.models import (
    Decision,
    Mission,
    MissionStatus,
    ProofCheck,
    Selection,
    Verdict,
    WakeCycle,
    WakeTrigger,
)
from proofpay.state import (
    IllegalTransition,
    InMemoryRepository,
    LEGAL_TRANSITIONS,
    MissionAlreadyExists,
    MissionNotFound,
    assert_transition,
    is_legal_transition,
)

ALL_STATUSES = list(MissionStatus)
LEGAL_PAIRS = [
    (src, dst) for src, dsts in LEGAL_TRANSITIONS.items() for dst in dsts
]
ILLEGAL_PAIRS = [
    (src, dst)
    for src, dst in product(ALL_STATUSES, ALL_STATUSES)
    if dst not in LEGAL_TRANSITIONS[src]
]


def make_mission(status: MissionStatus = MissionStatus.CREATED) -> Mission:
    return Mission(
        mission_id="m1", goal="g", budget_usd=6000, status=status
    )


# --------------------------------------------------------------------------- #
# Pure transition table
# --------------------------------------------------------------------------- #
def test_the_spec_lifecycle_is_the_legal_happy_path():
    # CREATED -> CONTRACTED -> FUNDED -> AWAITING_DELIVERY -> VERIFYING -> RELEASED
    chain = [
        MissionStatus.CREATED,
        MissionStatus.CONTRACTED,
        MissionStatus.FUNDED,
        MissionStatus.AWAITING_DELIVERY,
        MissionStatus.VERIFYING,
        MissionStatus.RELEASED,
    ]
    for a, b in zip(chain, chain[1:]):
        assert is_legal_transition(a, b)


@pytest.mark.parametrize("src,dst", LEGAL_PAIRS)
def test_legal_transition_allowed(src, dst):
    assert_transition(src, dst)  # does not raise


@pytest.mark.parametrize("src,dst", ILLEGAL_PAIRS)
def test_illegal_transition_raises(src, dst):
    with pytest.raises(IllegalTransition) as exc:
        assert_transition(src, dst)
    assert exc.value.current is src
    assert exc.value.target is dst


def test_terminal_states_have_no_exits():
    assert LEGAL_TRANSITIONS[MissionStatus.RELEASED] == frozenset()
    assert LEGAL_TRANSITIONS[MissionStatus.DISPUTED] == frozenset()


def test_no_self_transitions():
    for status in ALL_STATUSES:
        assert not is_legal_transition(status, status)


# --------------------------------------------------------------------------- #
# Repository: CRUD
# --------------------------------------------------------------------------- #
def test_create_and_get():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    got = repo.get_mission("m1")
    assert got.mission_id == "m1"
    assert got.status is MissionStatus.CREATED


def test_create_duplicate_raises():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    with pytest.raises(MissionAlreadyExists):
        repo.create_mission(make_mission())


def test_get_missing_raises():
    repo = InMemoryRepository()
    with pytest.raises(MissionNotFound):
        repo.get_mission("nope")


# --------------------------------------------------------------------------- #
# Repository: status transitions enforce the machine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src,dst", LEGAL_PAIRS)
def test_repo_legal_transition_updates_status(src, dst):
    repo = InMemoryRepository()
    repo.create_mission(make_mission(status=src))
    updated = repo.update_status("m1", dst)
    assert updated.status is dst
    assert repo.get_mission("m1").status is dst


@pytest.mark.parametrize("src,dst", ILLEGAL_PAIRS)
def test_repo_illegal_transition_raises_and_preserves_status(src, dst):
    repo = InMemoryRepository()
    repo.create_mission(make_mission(status=src))
    with pytest.raises(IllegalTransition):
        repo.update_status("m1", dst)
    # State must be untouched after a rejected transition.
    assert repo.get_mission("m1").status is src


def test_update_status_missing_mission_raises():
    repo = InMemoryRepository()
    with pytest.raises(MissionNotFound):
        repo.update_status("nope", MissionStatus.CONTRACTED)


def test_update_status_refreshes_updated_at():
    repo = InMemoryRepository()
    created = repo.create_mission(make_mission())
    updated = repo.update_status("m1", MissionStatus.CONTRACTED)
    assert updated.updated_at >= created.updated_at


# --------------------------------------------------------------------------- #
# Repository: patch non-status fields
# --------------------------------------------------------------------------- #
def test_patch_mission_updates_fields():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    patched = repo.patch_mission(
        "m1",
        offer_id="offer-7",
        engagement_id="eng-3",
        provider_name="Guanacaste Legal SA",
        selection=Selection(offer_id="offer-7", rationale="best collateral"),
    )
    assert patched.offer_id == "offer-7"
    assert patched.engagement_id == "eng-3"
    assert patched.selection.rationale == "best collateral"
    # status untouched
    assert patched.status is MissionStatus.CREATED


def test_patch_mission_rejects_status():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    with pytest.raises(ValueError):
        repo.patch_mission("m1", status=MissionStatus.CONTRACTED)


def test_patch_missing_mission_raises():
    repo = InMemoryRepository()
    with pytest.raises(MissionNotFound):
        repo.patch_mission("nope", offer_id="x")


# --------------------------------------------------------------------------- #
# Repository: wake subcollection
# --------------------------------------------------------------------------- #
def wake(wake_id: str, trigger: WakeTrigger = WakeTrigger.PUBSUB) -> WakeCycle:
    return WakeCycle(wake_id=wake_id, trigger=trigger)


def test_add_wakes_persisted_in_order():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    repo.add_wake("m1", wake("w1", WakeTrigger.CREATE))
    repo.add_wake("m1", wake("w2", WakeTrigger.PUBSUB))
    wakes = repo.get_wakes("m1")
    assert [w.wake_id for w in wakes] == ["w1", "w2"]
    assert wakes[0].trigger is WakeTrigger.CREATE


def test_save_wake_upserts_by_id():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    repo.add_wake("m1", wake("w1"))
    # Same wake_id evolves with proof checks and a verdict.
    evolved = WakeCycle(
        wake_id="w1",
        trigger=WakeTrigger.PUBSUB,
        proof_checks=[ProofCheck(step_id="s1", required_kind="k")],
        policy=Decision(verdict=Verdict.RELEASE),
    )
    repo.save_wake("m1", evolved)
    wakes = repo.get_wakes("m1")
    assert len(wakes) == 1
    assert wakes[0].policy.verdict is Verdict.RELEASE
    assert len(wakes[0].proof_checks) == 1


def test_save_wake_appends_when_new():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    repo.save_wake("m1", wake("w1"))
    repo.save_wake("m1", wake("w2"))
    assert [w.wake_id for w in repo.get_wakes("m1")] == ["w1", "w2"]


def test_wakes_on_missing_mission_raise():
    repo = InMemoryRepository()
    with pytest.raises(MissionNotFound):
        repo.add_wake("nope", wake("w1"))
    with pytest.raises(MissionNotFound):
        repo.get_wakes("nope")


def test_get_trace_bundles_mission_and_wakes():
    repo = InMemoryRepository()
    repo.create_mission(make_mission())
    repo.add_wake("m1", wake("w1"))
    trace = repo.get_trace("m1")
    assert trace.mission.mission_id == "m1"
    assert [w.wake_id for w in trace.wakes] == ["w1"]


# --------------------------------------------------------------------------- #
# Isolation: stored state is not aliased to caller references
# --------------------------------------------------------------------------- #
def test_returned_mission_is_isolated_from_store():
    repo = InMemoryRepository()
    got = repo.create_mission(make_mission())
    got.goal = "mutated by caller"
    assert repo.get_mission("m1").goal == "g"


def test_input_mission_is_isolated_from_store():
    repo = InMemoryRepository()
    m = make_mission()
    repo.create_mission(m)
    m.goal = "mutated after create"
    assert repo.get_mission("m1").goal == "g"
