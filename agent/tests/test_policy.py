"""Exhaustive tests for the release gate (SPEC §3).

``evaluate`` returns only RELEASE or DISPUTE; WAIT is the orchestration layer's
pre-delivery verdict and is not produced here. Coverage is per predicate: an
all-green baseline releases, each predicate is failed in isolation and in
combination, plus the veto scenarios the spec calls out (the LLM can veto a
release, never force one).
"""

from __future__ import annotations

import pytest

from proofpay import policy
from proofpay.models import (
    EngagementInfo,
    EngagementStep,
    Mission,
    ProofCheck,
    Verdict,
    VerifyError,
)

KIND = "company_registration"


# --------------------------------------------------------------------------- #
# Builders for an all-green scenario; tests mutate one axis at a time.
# --------------------------------------------------------------------------- #
def mission() -> Mission:
    return Mission(mission_id="m1", goal="Form a company in Costa Rica", budget_usd=6000)


def green_check(step_id: str = "s1", kind: str = KIND) -> ProofCheck:
    return ProofCheck(
        step_id=step_id,
        required_kind=kind,
        ref=f"CR-RN-2026-{step_id}",
        verified=True,
        verify_error=None,
        returned_kind=kind,
        llm_satisfies=True,
        llm_reason="matches the goal",
    )


def green_engagement(
    steps: tuple[str, ...] = ("s1",),
    escrow: int = 100000,
    release: int = 100000,
) -> EngagementInfo:
    # Happy-path Bufete Herrera engagement (CONTRACTS §3): 20% upfront = $1,000
    # held in escrow; release_amount is that committed upfront in cents.
    return EngagementInfo(
        engagement_id="eng-1",
        steps=[EngagementStep(step_id=s, required_kind=KIND) for s in steps],
        escrow_balance=escrow,
        release_amount=release,
    )


def evaluate(checks, engagement):
    return policy.evaluate(mission(), checks, engagement)


# --------------------------------------------------------------------------- #
# All green
# --------------------------------------------------------------------------- #
def test_all_green_releases():
    d = evaluate([green_check()], green_engagement())
    assert d.verdict is Verdict.RELEASE
    assert d.failed_predicates == []


def test_all_green_multi_step_releases():
    checks = [green_check("s1"), green_check("s2")]
    d = evaluate(checks, green_engagement(steps=("s1", "s2")))
    assert d.verdict is Verdict.RELEASE
    assert d.failed_predicates == []


# --------------------------------------------------------------------------- #
# P1 - every step has at least one proof
# --------------------------------------------------------------------------- #
def test_p1_missing_proof_for_a_step():
    checks = [green_check("s1")]
    d = evaluate(checks, green_engagement(steps=("s1", "s2")))
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P1"]


def test_p1_empty_checks_with_steps():
    # No proofs at all (a deadline-passed non-delivery reaches the gate this way).
    d = evaluate([], green_engagement(steps=("s1",)))
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P1"]


def test_p1_empty_engagement_never_releases():
    # No steps must not release vacuously (money-safety guard).
    d = evaluate([], green_engagement(steps=()))
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P1"]


def test_p1_empty_engagement_steps_with_stray_proof_never_releases():
    d = evaluate([green_check("s_stray")], green_engagement(steps=()))
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P1"]


def test_p1_extra_proof_for_unknown_step_still_covers_known_steps():
    checks = [green_check("s1"), green_check("s_stray")]
    d = evaluate(checks, green_engagement(steps=("s1",)))
    assert d.verdict is Verdict.RELEASE


# --------------------------------------------------------------------------- #
# P2 - registry-anchored proofs verified this wake
# --------------------------------------------------------------------------- #
def test_p2_unverified_registry_proof_disputes():
    bad = green_check().model_copy(
        update={"verified": False, "returned_kind": None, "llm_satisfies": None,
                "verify_error": VerifyError.NOT_FOUND}
    )
    d = evaluate([bad], green_engagement())
    # Unverified: only P2 flags (P3/P4 are scoped to verified proofs).
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P2"]


def test_p2_nonexistent_reference_is_fraud_path():
    fraud = ProofCheck(
        step_id="s1",
        required_kind=KIND,
        ref="CR-RN-2026-999999",
        verified=False,
        verify_error=VerifyError.NOT_FOUND,
    )
    d = evaluate([fraud], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert "P2" in d.failed_predicates


@pytest.mark.parametrize("err", [VerifyError.NOT_FOUND, VerifyError.UNAVAILABLE])
def test_p2_fails_for_both_404_and_502(err):
    # A 404 (fraud) and a 502 (registry unavailable) both fail P2; the decision
    # is identical, but the error kind on the proof keeps them distinguishable
    # for the dispute reason and trace.
    unverified = green_check().model_copy(
        update={"verified": False, "returned_kind": None,
                "llm_satisfies": None, "verify_error": err}
    )
    d = evaluate([unverified], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P2"]


def test_p2_ignores_non_registry_proofs():
    # A proof with no ref is not registry-anchored; P2 does not apply, but it
    # still satisfies P1 for its step.
    non_registry = ProofCheck(
        step_id="s1",
        required_kind=KIND,
        ref=None,
        verified=False,
        returned_kind=None,
        llm_satisfies=None,
    )
    d = evaluate([non_registry], green_engagement())
    assert d.verdict is Verdict.RELEASE
    assert d.failed_predicates == []


# --------------------------------------------------------------------------- #
# P3 - returned kind matches required kind
# --------------------------------------------------------------------------- #
def test_p3_kind_mismatch_disputes():
    mismatched = green_check().model_copy(update={"returned_kind": "land_title"})
    d = evaluate([mismatched], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P3"]


def test_p3_only_applies_to_verified_proofs():
    unverified = green_check().model_copy(
        update={"verified": False, "returned_kind": None, "llm_satisfies": None,
                "verify_error": VerifyError.NOT_FOUND}
    )
    d = evaluate([unverified], green_engagement())
    assert d.failed_predicates == ["P2"]


# --------------------------------------------------------------------------- #
# P4 - judge verdict satisfies=true (advisory; can veto, never force)
# --------------------------------------------------------------------------- #
def test_p4_judge_says_no_disputes():
    vetoed = green_check().model_copy(update={"llm_satisfies": False})
    d = evaluate([vetoed], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P4"]


def test_p4_missing_verdict_on_verified_proof_disputes():
    missing = green_check().model_copy(update={"llm_satisfies": None})
    d = evaluate([missing], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P4"]


def test_p4_true_cannot_force_release_when_p2_fails():
    forced = ProofCheck(
        step_id="s1",
        required_kind=KIND,
        ref="CR-RN-2026-x",
        verified=False,        # P2 fails
        verify_error=VerifyError.NOT_FOUND,
        returned_kind=None,
        llm_satisfies=True,    # LLM tries to say yes
        llm_reason="looks legit to me",
    )
    d = evaluate([forced], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert "P2" in d.failed_predicates


def test_p4_true_cannot_force_release_when_p3_fails():
    forced = green_check().model_copy(
        update={"returned_kind": "land_title", "llm_satisfies": True}
    )
    d = evaluate([forced], green_engagement())
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P3"]


# --------------------------------------------------------------------------- #
# P5 - escrow covers the release (integer cents)
# --------------------------------------------------------------------------- #
def test_p5_insufficient_escrow_disputes():
    d = evaluate([green_check()], green_engagement(escrow=99999, release=100000))
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P5"]


def test_p5_exact_escrow_releases():
    d = evaluate([green_check()], green_engagement(escrow=100000, release=100000))
    assert d.verdict is Verdict.RELEASE


def test_p5_surplus_escrow_releases():
    d = evaluate([green_check()], green_engagement(escrow=250000, release=100000))
    assert d.verdict is Verdict.RELEASE


# --------------------------------------------------------------------------- #
# Combinations and ordering
# --------------------------------------------------------------------------- #
def test_multiple_failures_recorded_in_predicate_order():
    # s1: kind mismatch (P3). s2: judge vetoes (P4). escrow short (P5).
    check_p3 = green_check("s1").model_copy(update={"returned_kind": "land_title"})
    check_p4 = green_check("s2").model_copy(update={"llm_satisfies": False})
    d = evaluate(
        [check_p3, check_p4],
        green_engagement(steps=("s1", "s2"), escrow=1, release=100000),
    )
    assert d.verdict is Verdict.DISPUTE
    assert d.failed_predicates == ["P3", "P4", "P5"]


def test_p1_and_p5_together():
    d = evaluate(
        [green_check("s1")],
        green_engagement(steps=("s1", "s2"), escrow=1, release=100000),
    )
    assert d.failed_predicates == ["P1", "P5"]


# --------------------------------------------------------------------------- #
# Structural invariants of the gate
# --------------------------------------------------------------------------- #
def test_evaluate_only_returns_release_or_dispute():
    for checks, eng in [
        ([green_check()], green_engagement()),
        ([], green_engagement(steps=("s1",))),
        ([green_check().model_copy(update={"llm_satisfies": False})], green_engagement()),
        ([], green_engagement(steps=())),
    ]:
        assert policy.evaluate(mission(), checks, eng).verdict in {
            Verdict.RELEASE,
            Verdict.DISPUTE,
        }


def test_release_only_with_empty_failed_predicates():
    d = evaluate([green_check()], green_engagement())
    assert (d.verdict is Verdict.RELEASE) == (d.failed_predicates == [])
