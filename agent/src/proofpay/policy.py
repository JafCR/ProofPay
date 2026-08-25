"""The deterministic release gate (SPEC §3) - the load-bearing module.

``policy.py`` is pure and synchronous: no I/O, no network, no clock, no model.
It is the ONLY code path that may authorize ``approve_and_release_payment``
(docs/SPEC.md §3). It expresses that structurally by producing a
:class:`Decision`; callers (``orchestrator.py`` / ``agent.py`` / ``main.py``) act
solely on ``Decision.verdict`` and treat ``RELEASE`` as the single license to
release payment. The LLM's advisory verdicts arrive as data on the proof checks
and can only ever *veto* a release (P4), never force one.

``evaluate`` returns only ``RELEASE`` or ``DISPUTE``. WAIT is a wake-level verdict
the orchestration layer records when a sweep fires before delivery and before the
deadline - in that case the gate is not run at all (DECISIONS.md 2026-08-25). Once
the gate runs, either every predicate holds (RELEASE) or it disputes.

Release iff ALL predicates hold, evaluated over data the agent fetched itself in
the CURRENT wake:

- P1  Every fulfillment step has at least one submitted proof. An engagement with
      no steps is not a deliverable and disputes (never a vacuous release).
- P2  Every registry-anchored proof was verified in this wake (a record returned,
      no error). A 404 (does-not-exist) and a 502 (registry unavailable) both
      fail P2; the distinction (``ProofCheck.verify_error``) is carried, not
      decided here.
- P3  For every verified proof, ``returned_kind == required_kind``.
- P4  Every verified proof's judge verdict is ``satisfies=true``.
- P5  Escrow covers the release: ``escrow_balance >= release_amount`` (integer
      cents; the caller sets ``release_amount`` to the committed upfront).

Anything else is a DISPUTE, with the failing predicate ids recorded in P1..P5
order. There is no retry-until-release loop.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import Decision, EngagementInfo, Mission, ProofCheck, Verdict


def _p1_every_step_has_proof(
    checks: Sequence[ProofCheck], engagement: EngagementInfo
) -> bool:
    # An engagement with no fulfillment steps is not a deliverable to release on:
    # there is nothing proving delivery, so P1 fails rather than passing vacuously.
    if not engagement.steps:
        return False
    proven_steps = {c.step_id for c in checks}
    return all(step.step_id in proven_steps for step in engagement.steps)


def _p2_registry_proofs_verified(checks: Sequence[ProofCheck]) -> bool:
    # Only registry-anchored proofs must clear verify_registry_reference. A 404 or
    # a 502 both land here as verified=False (see ProofCheck.verify_error).
    return all(c.verified for c in checks if c.registry_anchored)


def _p3_kinds_match(checks: Sequence[ProofCheck]) -> bool:
    # Kind is only meaningful for a proof that returned a record this wake.
    return all(
        c.returned_kind == c.required_kind for c in checks if c.verified
    )


def _p4_judge_satisfied(checks: Sequence[ProofCheck]) -> bool:
    # The judge assesses verified records; each must come back satisfies=true.
    # A missing verdict (None) on a verified proof is treated as not satisfied.
    return all(c.llm_satisfies is True for c in checks if c.verified)


def _p5_escrow_covers_release(engagement: EngagementInfo) -> bool:
    return engagement.escrow_balance >= engagement.release_amount


# Ordered so failed_predicates is deterministic (P1..P5).
_PREDICATES = (
    ("P1", lambda checks, eng: _p1_every_step_has_proof(checks, eng)),
    ("P2", lambda checks, eng: _p2_registry_proofs_verified(checks)),
    ("P3", lambda checks, eng: _p3_kinds_match(checks)),
    ("P4", lambda checks, eng: _p4_judge_satisfied(checks)),
    ("P5", lambda checks, eng: _p5_escrow_covers_release(eng)),
)


def evaluate(
    mission: Mission,
    checks: Sequence[ProofCheck],
    engagement: EngagementInfo,
) -> Decision:
    """Decide whether payment may be released for the current wake.

    Returns a :class:`Decision` whose ``verdict`` is ``RELEASE`` only when every
    predicate P1-P5 holds over ``checks`` and ``engagement`` (the data gathered
    this wake); otherwise ``DISPUTE`` with the ids of the predicates that failed,
    in P1..P5 order. Never returns ``WAIT`` (that is the orchestration layer's
    pre-delivery verdict) and never raises for ordinary data.

    ``mission`` is accepted for symmetry with the spec's signature and future
    predicates; the release decision depends only on the current wake's evidence.
    """
    failed = [
        pid for pid, holds in _PREDICATES if not holds(checks, engagement)
    ]
    verdict = Verdict.RELEASE if not failed else Verdict.DISPUTE
    return Decision(verdict=verdict, failed_predicates=failed)


__all__ = ["evaluate"]
