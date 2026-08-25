"""Wake orchestration (SPEC §2.2) — the glue between the HTTP layer and the gate.

``main.py`` owns the FastAPI surface; ``agent.py`` owns the concrete
:class:`Marketplace` (``PactaMarketplace`` over the unmodified MCP server);
``judge.py`` owns the Gemini judgment calls; ``policy.py`` owns the release gate.
This module runs the two wake cycles and is deliberately the one place where
The project's hard rule (docs/SPEC.md §3) is honoured structurally:

    ``approve_and_release_payment`` is awaited from exactly one line, inside the
    ``Decision.verdict is RELEASE`` branch, and that verdict comes only from
    ``policy.evaluate``. The judge's verdicts arrive as data on the proof checks
    and can only veto a release (P4), never trigger one.

The gate stays pure: this module computes ``delivered`` (the engagement reached
submission) and ``deadline_passed`` (the delivery window elapsed) and records the
pre-delivery WAIT verdict itself, without running the gate. Only when the work is
delivered — or the deadline forces a decision — does it call
``policy.evaluate(mission, checks, engagement)``, which returns RELEASE or DISPUTE
and nothing else (DECISIONS.md 2026-08-25).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Protocol, runtime_checkable

from . import policy
from .judge import Judge, StepRequirement
from .models import (
    VerifyError,
    ActionRecord,
    Decision,
    EngagementInfo,
    EngagementStep,
    Mission,
    MissionStatus,
    ProofCheck,
    Verdict,
    WakeCycle,
    WakeTrigger,
    utcnow,
)
from .settings import Settings
from .state import MissionRepository

#: Engagement states in which the provider has handed the work back for
#: verification (CONTRACTS.md §5). ``policy`` treats this as ``delivered``.
_DELIVERED_STATES = frozenset({"submitted", "completed", "disputed", "resolved"})


class MarketplaceError(Exception):
    """A marketplace tool call failed (any non-2xx from the underlying REST/MCP)."""


class RegistryUnavailable(MarketplaceError):
    """The registry could not answer (Pacta HTTP 502).

    Distinct from "reference does not exist" (a ``None`` record): the protocol
    refuses to guess, so the gate must not treat the proof as verified. The
    orchestrator maps this to an unverified proof → P2 fails → DISPUTE.
    """


@runtime_checkable
class Marketplace(Protocol):
    """The Pacta marketplace as the agent uses it (implemented by ``agent.py``).

    Shapes:

    - ``search_offers`` returns offers in Pacta's **MCP summary shape**
      (CONTRACTS.md §3): nested ``provider``, money as ``"$5,000"`` strings — the
      judge parses them.
    - ``get_engagement`` returns the **normalized cents shape** (CONTRACTS.md §5):
      ``{engagement_id, state, price_cents, upfront_cents, escrow_balance_cents,
      provider_name, steps: [{step_id, position, title, required_kind, proof_text,
      registry_ref, verified_by_platform, status}]}`` — the gate needs integers.
    - ``verify_registry_reference`` → registry record dict, or ``None`` when the
      reference does not exist; raises :class:`RegistryUnavailable` on HTTP 502.
    """

    async def search_offers(self, query: str) -> list[dict]: ...

    async def create_engagement(self, offer_id: str) -> dict: ...

    async def agree_to_contract(self, engagement_id: str) -> dict: ...

    async def fund_escrow(self, engagement_id: str) -> dict: ...

    async def get_engagement(self, engagement_id: str) -> dict: ...

    async def verify_registry_reference(self, ref: str) -> dict | None: ...

    async def approve_and_release_payment(self, engagement_id: str) -> dict: ...

    async def reject_and_open_dispute(
        self, engagement_id: str, reason: str
    ) -> dict: ...

    async def rate_provider(self, engagement_id: str, value: str) -> dict: ...


def _new_wake_id() -> str:
    return uuid.uuid4().hex


class Orchestrator:
    """Runs Wake 1 (hire + fund) and Wake 2 (verify + settle)."""

    def __init__(
        self,
        repo: MissionRepository,
        marketplace: Marketplace,
        judge: Judge,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._mp = marketplace
        self._judge = judge
        self._settings = settings

    # ---- Wake 1: discover → select → contract → fund → sleep --------------- #
    async def run_wake_one(self, mission_id: str) -> Mission:
        """Synchronous hire flow (SPEC §2.2 Wake 1). No release gate runs here;
        the mission is persisted at ``AWAITING_DELIVERY`` and the process exits."""
        mission = self._repo.get_mission(mission_id)
        wake = WakeCycle(wake_id=_new_wake_id(), trigger=WakeTrigger.CREATE)
        actions: list[ActionRecord] = []

        offers = await self._mp.search_offers(mission.goal)
        actions.append(
            ActionRecord(
                tool="search_offers",
                args={"query": mission.goal},
                detail=f"{len(offers)} offers",
            )
        )

        selection = self._judge.select_offer(
            mission.goal, offers, budget_usd=mission.budget_usd
        )
        chosen = _find_offer(offers, selection.offer_id)
        self._repo.patch_mission(
            mission_id,
            selection=selection,
            offer_id=str(selection.offer_id),
            provider_name=_provider_name(chosen),
        )
        actions.append(
            ActionRecord(
                tool="judge.select_offer",
                args={"offer_id": selection.offer_id},
                detail=selection.rationale,
            )
        )

        engagement = await self._mp.create_engagement(selection.offer_id)
        engagement_id = str(engagement["engagement_id"])
        actions.append(
            ActionRecord(tool="create_engagement", args={"offer_id": selection.offer_id})
        )

        await self._mp.agree_to_contract(engagement_id)
        self._repo.update_status(mission_id, MissionStatus.CONTRACTED)
        actions.append(ActionRecord(tool="agree_to_contract", args={"engagement_id": engagement_id}))

        await self._mp.fund_escrow(engagement_id)
        self._repo.update_status(mission_id, MissionStatus.FUNDED)
        actions.append(ActionRecord(tool="fund_escrow", args={"engagement_id": engagement_id}))

        self._repo.patch_mission(mission_id, engagement_id=engagement_id)
        self._repo.update_status(mission_id, MissionStatus.AWAITING_DELIVERY)

        wake.actions = actions
        wake.model = getattr(self._judge, "usage", None)
        wake.finished_at = utcnow()
        self._repo.add_wake(mission_id, wake)
        return self._repo.get_mission(mission_id)

    # ---- Wake 2: verify proofs → gate → release or dispute ---------------- #
    async def run_wake_two(
        self, mission_id: str, trigger: WakeTrigger
    ) -> WakeCycle:
        """Delivery/sweep handler (SPEC §2.2 Wake 2).

        Idempotent: a mission that is not ``AWAITING_DELIVERY`` (already settled or
        Wake 1 not finished) yields a no-op wake and no state change. Otherwise the
        gate decides: WAIT keeps the mission ``AWAITING_DELIVERY`` (no transition);
        RELEASE/DISPUTE moves it through ``VERIFYING`` to a terminal state."""
        mission = self._repo.get_mission(mission_id)
        if mission.status is not MissionStatus.AWAITING_DELIVERY:
            return WakeCycle(
                wake_id=_new_wake_id(),
                trigger=trigger,
                finished_at=utcnow(),
                actions=[
                    ActionRecord(
                        tool="noop",
                        ok=True,
                        detail=f"mission is {mission.status.value}, not AWAITING_DELIVERY",
                    )
                ],
            )

        wake = WakeCycle(wake_id=_new_wake_id(), trigger=trigger)
        actions: list[ActionRecord] = []
        engagement = await self._mp.get_engagement(mission.engagement_id or "")
        actions.append(
            ActionRecord(
                tool="get_engagement",
                args={"engagement_id": mission.engagement_id},
                detail=f"state={engagement.get('state')}",
            )
        )

        delivered = engagement.get("state") in _DELIVERED_STATES
        deadline_passed = self._deadline_passed(mission)

        if not delivered and not deadline_passed:
            # Still within the delivery window: WAIT is the orchestration layer's
            # pre-delivery verdict — the gate is not run (DECISIONS.md 2026-08-25).
            # No MCP verify calls, no state change; the agent just sleeps again.
            wake.policy = Decision(verdict=Verdict.WAIT)
            wake.actions = actions
            wake.model = getattr(self._judge, "usage", None)
            wake.finished_at = utcnow()
            self._repo.add_wake(mission_id, wake)
            return wake

        # Delivered, or the deadline forces a decision: re-verify everything.
        checks, verify_actions = await self._gather_proof_checks(engagement)
        actions.extend(verify_actions)
        engagement_info = _engagement_info(engagement)

        # THE gate. This is the sole authorization surface for a release.
        decision = policy.evaluate(mission, checks, engagement_info)
        wake.proof_checks = checks
        wake.policy = decision

        self._repo.update_status(mission_id, MissionStatus.VERIFYING)
        if decision.verdict is Verdict.RELEASE:
            await self._enact_release(engagement, actions)
            self._repo.update_status(mission_id, MissionStatus.RELEASED)
        else:  # DISPUTE
            await self._enact_dispute(engagement, checks, decision, actions)
            self._repo.update_status(mission_id, MissionStatus.DISPUTED)

        wake.actions = actions
        wake.model = getattr(self._judge, "usage", None)
        wake.finished_at = utcnow()
        self._repo.add_wake(mission_id, wake)
        return wake

    # ---- helpers ---------------------------------------------------------- #
    def _deadline_passed(self, mission: Mission) -> bool:
        deadline = mission.created_at + timedelta(
            seconds=self._settings.delivery_deadline_seconds
        )
        return utcnow() > deadline

    async def _gather_proof_checks(
        self, engagement: dict
    ) -> tuple[list[ProofCheck], list[ActionRecord]]:
        checks: list[ProofCheck] = []
        actions: list[ActionRecord] = []
        for step in engagement.get("steps", []):
            if not _step_has_proof(step):
                # No proof for this step → it is not among the checks, so P1
                # (every step proven) fails. Nothing to verify.
                continue
            required_kind = step.get("required_kind") or ""
            ref = step.get("registry_ref")
            check = ProofCheck(
                step_id=str(step["step_id"]),
                required_kind=required_kind,
                ref=ref,
            )
            if ref:
                record = None
                try:
                    record = await self._mp.verify_registry_reference(ref)
                    ok = True
                    detail = "record" if record else "no record (does not exist)"
                    if record is None:
                        check.verify_error = VerifyError.NOT_FOUND
                except RegistryUnavailable as exc:
                    ok = False
                    detail = f"registry unavailable: {exc}"
                    check.verify_error = VerifyError.UNAVAILABLE
                actions.append(
                    ActionRecord(
                        tool="verify_registry_reference",
                        args={"ref": ref},
                        ok=ok,
                        detail=detail,
                    )
                )
                if record:
                    check.verified = True
                    check.returned_kind = record.get("kind")
                    assessment = self._judge.assess_proof(
                        StepRequirement(
                            required_kind=required_kind,
                            description=step.get("title") or "",
                        ),
                        record,
                    )
                    check.llm_satisfies = assessment.satisfies
                    check.llm_reason = assessment.reason
                    actions.append(
                        ActionRecord(
                            tool="judge.assess_proof",
                            args={"step_id": check.step_id},
                            ok=assessment.satisfies,
                            detail=assessment.reason,
                        )
                    )
            checks.append(check)
        return checks, actions

    async def _enact_release(
        self, engagement: dict, actions: list[ActionRecord]
    ) -> None:
        engagement_id = str(engagement["engagement_id"])
        await self._mp.approve_and_release_payment(engagement_id)
        actions.append(
            ActionRecord(tool="approve_and_release_payment", args={"engagement_id": engagement_id})
        )
        try:
            await self._mp.rate_provider(engagement_id, "good")
            actions.append(
                ActionRecord(tool="rate_provider", args={"engagement_id": engagement_id, "value": "good"})
            )
        except MarketplaceError as exc:
            # Rating is post-settlement courtesy; a failure must not undo a release.
            actions.append(
                ActionRecord(tool="rate_provider", ok=False, detail=str(exc))
            )

    async def _enact_dispute(
        self,
        engagement: dict,
        checks: list[ProofCheck],
        decision: Decision,
        actions: list[ActionRecord],
    ) -> None:
        engagement_id = str(engagement["engagement_id"])
        reason = self._judge.draft_dispute(_mismatches(checks, decision))
        try:
            await self._mp.reject_and_open_dispute(engagement_id, reason)
            actions.append(
                ActionRecord(
                    tool="reject_and_open_dispute",
                    args={"engagement_id": engagement_id},
                    detail=reason,
                )
            )
        except MarketplaceError as exc:
            # Pacta only allows /reject from the 'submitted' state (CONTRACTS.md
            # §4). A non-delivery dispute cannot be enacted on the marketplace; the
            # agent's verdict still stands and the mission is marked DISPUTED.
            actions.append(
                ActionRecord(
                    tool="reject_and_open_dispute",
                    args={"engagement_id": engagement_id},
                    ok=False,
                    detail=f"could not enact on marketplace: {exc}",
                )
            )


# --------------------------------------------------------------------------- #
# Pure mapping helpers (offer / engagement dict → gate models)
# --------------------------------------------------------------------------- #
def _find_offer(offers: list[dict], offer_id: str) -> dict | None:
    return next(
        (o for o in offers if str(o.get("offer_id")) == str(offer_id)), None
    )


def _provider_name(offer: dict | None) -> str | None:
    if not offer:
        return None
    provider = offer.get("provider") or {}
    return provider.get("name") or offer.get("provider_name")


def _step_has_proof(step: dict) -> bool:
    return bool(step.get("proof_text")) or step.get("status") == "done"


def _engagement_info(engagement: dict) -> EngagementInfo:
    steps = [
        EngagementStep(
            step_id=str(s["step_id"]),
            required_kind=s.get("required_kind") or "",
        )
        for s in engagement.get("steps", [])
    ]
    # P5 (DECISIONS.md 2026-08-25): Pacta's escrow only ever holds the upfront (the
    # remainder is drawn atomically at approval), so the gate checks the escrow
    # still holds that committed downpayment: escrow_balance >= release_amount,
    # with release_amount set to the raw REST upfront_cents.
    return EngagementInfo(
        engagement_id=str(engagement["engagement_id"]),
        steps=steps,
        escrow_balance=int(engagement.get("escrow_balance_cents", 0) or 0),
        release_amount=int(engagement.get("upfront_cents", 0) or 0),
    )


def _mismatches(checks: list[ProofCheck], decision: Decision) -> list[dict]:
    """Failing proof checks as ``judge.Mismatch``-compatible dicts (key ``issue``).

    Falls back to the failed predicate ids when there is no per-proof finding — e.g.
    a non-delivery dispute where P1 failed with no proofs at all."""
    out: list[dict] = []
    for c in checks:
        issue = None
        if c.registry_anchored and not c.verified:
            issue = "reference did not verify against the registry"
        elif c.verified and c.returned_kind != c.required_kind:
            issue = "registry record kind does not match the required kind"
        elif c.verified and c.llm_satisfies is not True:
            issue = c.llm_reason or "proof does not satisfy the requirement"
        if issue:
            out.append(
                {
                    "step_id": c.step_id,
                    "required_kind": c.required_kind,
                    "returned_kind": c.returned_kind,
                    "issue": issue,
                }
            )
    if not out:
        out.append(
            {"issue": "delivery incomplete: " + ", ".join(decision.failed_predicates)}
        )
    return out


__all__ = [
    "MarketplaceError",
    "RegistryUnavailable",
    "Marketplace",
    "Orchestrator",
]
