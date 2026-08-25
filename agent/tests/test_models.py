"""Model tests (SPEC §4): required fields, enums, and JSON round-trips."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from proofpay.models import (
    Decision,
    EngagementInfo,
    EngagementStep,
    Mission,
    MissionStatus,
    MissionTrace,
    ModelUsage,
    ProofCheck,
    RejectedOffer,
    Selection,
    Verdict,
    VerifyError,
    WakeCycle,
    WakeTrigger,
)


# --------------------------------------------------------------------------- #
# Enums carry the exact spec strings
# --------------------------------------------------------------------------- #
def test_mission_status_values():
    assert [s.value for s in MissionStatus] == [
        "CREATED",
        "CONTRACTED",
        "FUNDED",
        "AWAITING_DELIVERY",
        "VERIFYING",
        "RELEASED",
        "DISPUTED",
    ]


def test_verdict_values():
    assert {v.value for v in Verdict} == {"RELEASE", "DISPUTE", "WAIT"}


def test_wake_trigger_values():
    assert {t.value for t in WakeTrigger} == {"create", "pubsub", "sweep"}


# --------------------------------------------------------------------------- #
# Required fields
# --------------------------------------------------------------------------- #
def test_mission_requires_core_fields():
    with pytest.raises(ValidationError):
        Mission()  # missing mission_id, goal, budget_usd


def test_mission_defaults():
    m = Mission(mission_id="m1", goal="do a thing", budget_usd=6000)
    assert m.status is MissionStatus.CREATED
    assert m.offer_id is None
    assert m.engagement_id is None
    assert m.selection is None
    assert m.created_at.tzinfo is not None
    assert m.updated_at.tzinfo is not None


def test_proofcheck_requires_step_and_kind():
    with pytest.raises(ValidationError):
        ProofCheck(ref="CR-RN-2026-1")  # missing step_id, required_kind


def test_decision_requires_verdict():
    with pytest.raises(ValidationError):
        Decision()


def test_wakecycle_requires_id_and_trigger():
    with pytest.raises(ValidationError):
        WakeCycle()


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Mission(mission_id="m1", goal="g", budget_usd=1, bogus="x")


# --------------------------------------------------------------------------- #
# ProofCheck.registry_anchored
# --------------------------------------------------------------------------- #
def test_registry_anchored_true_when_ref_present():
    pc = ProofCheck(step_id="s1", required_kind="company_registration", ref="CR-1")
    assert pc.registry_anchored is True


@pytest.mark.parametrize("ref", [None, ""])
def test_registry_anchored_false_without_ref(ref):
    pc = ProofCheck(step_id="s1", required_kind="k", ref=ref)
    assert pc.registry_anchored is False


def test_proofcheck_verify_error_defaults_none():
    pc = ProofCheck(step_id="s1", required_kind="k")
    assert pc.verify_error is None


def test_verify_error_values():
    assert {e.value for e in VerifyError} == {"not_found", "unavailable"}


# --------------------------------------------------------------------------- #
# Decision is frozen (gate output must not be mutated)
# --------------------------------------------------------------------------- #
def test_decision_is_frozen():
    d = Decision(verdict=Verdict.RELEASE)
    with pytest.raises(ValidationError):
        d.verdict = Verdict.DISPUTE


# --------------------------------------------------------------------------- #
# JSON round-trips
# --------------------------------------------------------------------------- #
def test_mission_round_trip():
    m = Mission(
        mission_id="m1",
        goal="Form a company in Costa Rica",
        budget_usd=6000,
        status=MissionStatus.AWAITING_DELIVERY,
        offer_id="offer-7",
        engagement_id="eng-3",
        provider_name="Guanacaste Legal SA",
        selection=Selection(
            offer_id="offer-7",
            rationale="highest collateral and rating",
            rejected=[RejectedOffer(offer_id="offer-2", reason="zero collateral")],
        ),
    )
    restored = Mission.model_validate_json(m.model_dump_json())
    assert restored == m


def test_wakecycle_round_trip():
    w = WakeCycle(
        wake_id="w1",
        trigger=WakeTrigger.PUBSUB,
        proof_checks=[
            ProofCheck(
                step_id="s1",
                required_kind="company_registration",
                ref="CR-RN-2026-1",
                verified=True,
                verify_error=None,
                returned_kind="company_registration",
                llm_satisfies=True,
                llm_reason="matches goal",
            )
        ],
        policy=Decision(verdict=Verdict.RELEASE, failed_predicates=[]),
        model=ModelUsage(name="gemini-3.5-flash", tokens_in=10, tokens_out=20),
    )
    restored = WakeCycle.model_validate_json(w.model_dump_json())
    assert restored == w


def test_engagement_info_round_trip():
    e = EngagementInfo(
        engagement_id="eng-3",
        steps=[EngagementStep(step_id="s1", required_kind="company_registration")],
        escrow_balance=100000,
        release_amount=100000,
    )
    restored = EngagementInfo.model_validate_json(e.model_dump_json())
    assert restored == e


def test_engagement_info_amount_defaults_zero():
    e = EngagementInfo(engagement_id="eng-1")
    assert (e.escrow_balance, e.release_amount) == (0, 0)


def test_mission_trace_round_trip():
    trace = MissionTrace(
        mission=Mission(mission_id="m1", goal="g", budget_usd=100),
        wakes=[WakeCycle(wake_id="w1", trigger=WakeTrigger.CREATE)],
    )
    restored = MissionTrace.model_validate_json(trace.model_dump_json())
    assert restored == trace
