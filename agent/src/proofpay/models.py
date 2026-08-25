"""Pydantic data model for ProofPay (SPEC §4).

Field names mirror the Firestore documents exactly so persistence in ``state.py``
is a straight ``model_dump`` / ``model_validate`` round-trip. The models here are
plain data; the only behaviour is the :meth:`ProofCheck.registry_anchored`
convenience and validation.

Engagement money amounts are integer **cents**, matching Pacta's ``*_cents`` REST
fields (docs/CONTRACTS.md §5); the mission ``budget_usd`` stays in whole dollars
per the spec's data model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now; single source for default timestamps."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class MissionStatus(str, Enum):
    """Lifecycle of a mission (SPEC §4). Transitions are enforced in ``state.py``."""

    CREATED = "CREATED"
    CONTRACTED = "CONTRACTED"
    FUNDED = "FUNDED"
    AWAITING_DELIVERY = "AWAITING_DELIVERY"
    VERIFYING = "VERIFYING"
    RELEASED = "RELEASED"
    DISPUTED = "DISPUTED"


class Verdict(str, Enum):
    """Policy / wake verdict (SPEC §3, §4).

    ``policy.evaluate`` returns ``WAIT`` when the wake fired before delivery and
    before the deadline (keep sleeping; no funds move), and ``RELEASE`` or
    ``DISPUTE`` once delivery is in or the deadline has passed.
    """

    RELEASE = "RELEASE"
    DISPUTE = "DISPUTE"
    WAIT = "WAIT"


class WakeTrigger(str, Enum):
    """What woke the agent for a given wake cycle (SPEC §4)."""

    CREATE = "create"
    PUBSUB = "pubsub"
    SWEEP = "sweep"


class VerifyError(str, Enum):
    """Why ``verify_registry_reference`` failed for a proof (CONTRACTS §6).

    ``None`` on a proof check means the registry lookup succeeded (a record came
    back) or the proof was not registry-anchored. When set, it distinguishes a
    404 ("does not exist" — the fraud pattern) from a 502 ("registry could not
    decide" — not fraud). Both fail P2 identically; the distinction is only for
    the dispute reason and the trace.
    """

    NOT_FOUND = "not_found"      # 404: no such reference (fraud pattern)
    UNAVAILABLE = "unavailable"  # 502: registry unavailable, cannot decide


# --------------------------------------------------------------------------- #
# Offer selection (SPEC §2.2 Wake 1, §4 mission.selection)
# --------------------------------------------------------------------------- #
class RejectedOffer(BaseModel):
    """One offer the judge passed over, with its reason."""

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    reason: str


class Selection(BaseModel):
    """The judge's offer choice: ``{offer_id, rationale, rejected[]}``."""

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    rationale: str
    rejected: list[RejectedOffer] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Proof checks and engagement (SPEC §3, §4)
# --------------------------------------------------------------------------- #
class ProofCheck(BaseModel):
    """One submitted proof, as gathered during the current wake.

    Fields from SPEC §4
    (``{step_id, required_kind, ref, verified, returned_kind, llm_satisfies, llm_reason}``)
    plus ``verify_error`` (CONTRACTS §6) which records *why* a registry check
    failed.

    Mapping from the raw Pacta engagement step (CONTRACTS §5) that the caller
    normalizes into this shape:

    - ``step_id``      ← raw step ``id``
    - ``required_kind``← raw step ``verification_kind`` (MCP: ``requires_registry_proof``)
    - ``ref``          ← raw step ``proof_registry_ref``
    - ``verified``     ← whether the agent's OWN ``verify_registry_reference`` call
      returned a record this wake (independent of the platform's ``proof_verified``)
    - ``returned_kind``← ``kind`` from that verify record; ``None`` when unverified
    - ``verify_error`` ← 404 → ``NOT_FOUND`` / 502 → ``UNAVAILABLE`` / success → ``None``

    ``verified`` drives P2. ``verify_error`` keeps the 404-vs-502 distinction for
    the dispute reason and trace; both fail P2, but they are not the same story.
    ``llm_satisfies`` / ``llm_reason`` come from ``judge.assess_proof`` and are
    advisory only (P4 can veto a release, never force one).
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    required_kind: str
    ref: str | None = None
    verified: bool = False
    verify_error: VerifyError | None = None
    returned_kind: str | None = None
    llm_satisfies: bool | None = None
    llm_reason: str | None = None

    @property
    def registry_anchored(self) -> bool:
        """A proof is registry-anchored iff it carries a registry reference."""
        return bool(self.ref)


class EngagementStep(BaseModel):
    """A fulfillment step of an engagement — the policy's view (SPEC §3 P1, P3).

    This is the minimal projection the gate needs, not a full mirror of Pacta's
    raw step (CONTRACTS §5, which also carries ``position``, ``description``,
    ``status`` pending|done, ``proof_text``, ``completed_at``, ``proof_verified``).
    The caller maps: ``step_id`` ← raw step ``id``; ``required_kind`` ← raw
    ``verification_kind`` (MCP: ``requires_registry_proof``). Delivery status and
    the proof itself live on the matching :class:`ProofCheck`, not here.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    required_kind: str


class EngagementInfo(BaseModel):
    """The ``get_engagement`` view the policy gate needs (SPEC §3 P1, P5).

    Both amounts are integer **cents**, read from the raw Pacta REST engagement
    (CONTRACTS §5, §9 note 3); the caller normalizes and the policy never parses
    ``"$5,000"``. P5 holds iff ``escrow_balance >= release_amount``.

    The caller sets ``release_amount`` to the committed upfront (Pacta's
    ``upfront_cents``): the escrow only ever holds the upfront — ``fund_escrow``
    moves upfront%, the remainder is drawn atomically at approve — so an escrow
    that still covers the upfront means the full-price release is backed. Setting
    ``release_amount`` to the full price would dispute every happy-path release
    (DECISIONS.md 2026-08-25).
    """

    model_config = ConfigDict(extra="forbid")

    engagement_id: str
    steps: list[EngagementStep] = Field(default_factory=list)
    escrow_balance: int = 0
    release_amount: int = 0


# --------------------------------------------------------------------------- #
# Policy decision (SPEC §3) — the release gate's output
# --------------------------------------------------------------------------- #
class Decision(BaseModel):
    """Output of ``policy.evaluate`` and the value stored as ``wake.policy``.

    This is the *only* authorization surface for releasing payment: callers act
    on ``verdict`` and must treat a ``RELEASE`` here as the sole license to call
    ``approve_and_release_payment`` (docs/SPEC.md §3, the project's hard rule that
    the gate is the only path to a release). Frozen so a decision cannot be
    mutated after the gate produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    failed_predicates: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Wake cycle (SPEC §4 subcollection missions/{id}/wakes/{wake_id})
# --------------------------------------------------------------------------- #
class ModelUsage(BaseModel):
    """Token accounting for a wake's model calls (SPEC §4 wake.model)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    tokens_in: int = 0
    tokens_out: int = 0


class ActionRecord(BaseModel):
    """One tool call made during a wake (SPEC §4 wake.actions)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict = Field(default_factory=dict)
    ok: bool = True
    detail: str | None = None


class WakeCycle(BaseModel):
    """One wake of the agent (SPEC §4 subcollection document)."""

    model_config = ConfigDict(extra="forbid")

    wake_id: str
    trigger: WakeTrigger
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    proof_checks: list[ProofCheck] = Field(default_factory=list)
    policy: Decision | None = None
    actions: list[ActionRecord] = Field(default_factory=list)
    model: ModelUsage | None = None


# --------------------------------------------------------------------------- #
# Mission (SPEC §4 collection missions/{mission_id})
# --------------------------------------------------------------------------- #
class Mission(BaseModel):
    """A procurement mission document (SPEC §4).

    Wakes live in a subcollection and are not embedded here; use
    :class:`MissionTrace` when a caller needs the mission together with its wakes.
    """

    model_config = ConfigDict(extra="forbid")

    mission_id: str
    goal: str
    budget_usd: int
    status: MissionStatus = MissionStatus.CREATED
    offer_id: str | None = None
    engagement_id: str | None = None
    provider_name: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    selection: Selection | None = None


class MissionTrace(BaseModel):
    """A mission plus its wakes — the shape ``GET /missions/{id}`` returns."""

    model_config = ConfigDict(extra="forbid")

    mission: Mission
    wakes: list[WakeCycle] = Field(default_factory=list)


__all__ = [
    "utcnow",
    "MissionStatus",
    "Verdict",
    "WakeTrigger",
    "VerifyError",
    "RejectedOffer",
    "Selection",
    "ProofCheck",
    "EngagementStep",
    "EngagementInfo",
    "Decision",
    "ModelUsage",
    "ActionRecord",
    "WakeCycle",
    "Mission",
    "MissionTrace",
]
