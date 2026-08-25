"""Wiring tests for agent.py — no network, no Gemini, no ADK, no node.

The MCP client and the judge are fakes; the repository is the real
``InMemoryRepository`` (its state machine is exercised for real). The single REST
read (``_fetch_engagement_cents``) is monkeypatched. These tests pin the properties
that matter most:

- Wake 1 persists the mission to ``AWAITING_DELIVERY`` and calls the MCP tools in
  the contracted order (and never ``wait_for_provider_submission``).
- Wake 2 releases only when every predicate holds, disputes (and NEVER approves)
  when a registry proof fails to verify or the kind mismatches, and returns ``WAIT``
  without running the gate when a sweep fires before the delivery deadline.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from proofpay import agent
from proofpay.agent import ToolResult, run_wake1, run_wake2
from proofpay.judge import StubJudge
from proofpay.models import (
    Mission,
    MissionStatus,
    Verdict,
    VerifyError,
    WakeTrigger,
    utcnow,
)
from proofpay.settings import Settings
from proofpay.state import InMemoryRepository

SETTINGS = Settings(marketplace_url="http://localhost:3220", agent_id="1")

_OFFER = {
    "offer_id": 1,
    "title": "Establish a Costa Rican company",
    "price": "$5,000",
    "provider": {"name": "Bufete Herrera & Asociados", "collateral_at_stake": "$1,500"},
    "steps": [
        {"position": 1, "title": "Incorporate", "requires_registry_proof": "incorporation"},
        {"position": 2, "title": "Land eligibility", "requires_registry_proof": "land_eligibility"},
    ],
}


def _engagement(state: str, steps: list[dict]) -> dict:
    return {"engagement_id": 7, "state": state, "provider": "Bufete Herrera & Asociados", "steps": steps}


def _done_step(position: int, kind: str, ref: str) -> dict:
    return {
        "position": position,
        "title": f"step {position}",
        "status": "done",
        "proof": f"done. Ref {ref}.",
        "requires_registry_proof": kind,
        "registry_ref": ref,
        "verified_by_platform": True,
    }


class FakeMcp:
    """Records every call in order and returns scripted results.

    ``registry`` maps a ref -> (status, record). A ref absent from it is a 404.
    ``status`` 200 → ok with the record; 404/502 → an error envelope like Pacta's.
    """

    def __init__(self, *, offers=None, engagement=None, registry=None):
        self.calls: list[tuple[str, dict]] = []
        self._offers = offers if offers is not None else [_OFFER]
        self._engagement = engagement
        self._registry = registry or {}

    def _record(self, tool, **args):
        self.calls.append((tool, args))

    @property
    def tools(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def search_offers(self, query):
        self._record("search_offers", query=query)
        return ToolResult("search_offers", True, {"results": self._offers})

    async def create_engagement(self, offer_id):
        self._record("create_engagement", offer_id=offer_id)
        return ToolResult(
            "create_engagement", True,
            {"engagement_id": 7, "state": "draft", "provider": "Bufete Herrera & Asociados"},
        )

    async def agree_to_contract(self, engagement_id):
        self._record("agree_to_contract", engagement_id=engagement_id)
        return ToolResult("agree_to_contract", True, {"engagement_id": 7, "state": "agreed"})

    async def fund_escrow(self, engagement_id):
        self._record("fund_escrow", engagement_id=engagement_id)
        return ToolResult("fund_escrow", True, {"engagement_id": 7, "state": "funded"})

    async def get_engagement(self, engagement_id):
        self._record("get_engagement", engagement_id=engagement_id)
        return ToolResult("get_engagement", True, self._engagement)

    async def verify_registry_reference(self, ref):
        self._record("verify_registry_reference", ref=ref)
        status, record = self._registry.get(ref, (404, None))
        if status == 200:
            return ToolResult("verify_registry_reference", True, record)
        msg = "RegistryUnavailableError" if status == 502 else f"no public record with reference '{ref}'"
        return ToolResult(
            "verify_registry_reference", False, error=f"Error (HTTP {status}): {msg}"
        )

    async def approve_and_release_payment(self, engagement_id):
        self._record("approve_and_release_payment", engagement_id=engagement_id)
        return ToolResult("approve_and_release_payment", True, {"engagement_id": 7, "state": "completed"})

    async def reject_and_open_dispute(self, engagement_id, reason):
        self._record("reject_and_open_dispute", engagement_id=engagement_id, reason=reason)
        return ToolResult("reject_and_open_dispute", True, {"engagement_id": 7, "state": "disputed"})

    async def rate_provider(self, engagement_id, value):
        self._record("rate_provider", engagement_id=engagement_id, value=value)
        return ToolResult("rate_provider", True, {"engagement_id": 7})


def _patch_cents(monkeypatch, *, escrow=100000, upfront=100000):
    async def fake_cents(settings, engagement_id):
        return {"escrow_balance_cents": escrow, "upfront_cents": upfront, "price_cents": 500000}

    monkeypatch.setattr(agent, "_fetch_engagement_cents", fake_cents)


def _mk_mission(repo: InMemoryRepository, *, created_at=None) -> Mission:
    """A mission already at AWAITING_DELIVERY, as after Wake 1."""
    mission = Mission(
        mission_id="m1", goal="incorporate", budget_usd=6000,
        engagement_id="7", created_at=created_at or utcnow(),
    )
    repo.create_mission(mission)
    repo.update_status("m1", MissionStatus.CONTRACTED)
    repo.update_status("m1", MissionStatus.FUNDED)
    return repo.update_status("m1", MissionStatus.AWAITING_DELIVERY)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Wake 1
# --------------------------------------------------------------------------- #
def test_wake1_persists_and_calls_tools_in_order():
    repo = InMemoryRepository()
    mcp = FakeMcp()
    mission = run(
        run_wake1("incorporate a company", 6000, repo, StubJudge(SETTINGS),
                  client=mcp, settings=SETTINGS, mission_id="m1")
    )

    assert mission.status is MissionStatus.AWAITING_DELIVERY
    assert mission.offer_id == "1"
    assert mission.engagement_id == "7"
    assert mission.provider_name == "Bufete Herrera & Asociados"
    assert mission.selection is not None and mission.selection.offer_id == "1"

    assert mcp.tools == ["search_offers", "create_engagement", "agree_to_contract", "fund_escrow"]
    assert "wait_for_provider_submission" not in mcp.tools

    wakes = repo.get_wakes("m1")
    assert len(wakes) == 1
    assert wakes[0].trigger is WakeTrigger.CREATE
    assert [a.tool for a in wakes[0].actions] == mcp.tools


def test_wake1_selection_prefers_collateral():
    repo = InMemoryRepository()
    offers = [
        {"offer_id": 8, "title": "cheap", "price": "$900",
         "provider": {"name": "Despacho Sin Garantía"}, "steps": []},
        _OFFER,
    ]
    mission = run(
        run_wake1("incorporate", 6000, repo, StubJudge(SETTINGS),
                  client=FakeMcp(offers=offers), settings=SETTINGS, mission_id="m1")
    )
    assert mission.offer_id == "1"  # collateral-backed, not the $900 zero-collateral offer
    assert mission.selection.rejected


# --------------------------------------------------------------------------- #
# Wake 2 — happy path
# --------------------------------------------------------------------------- #
def test_wake2_all_green_releases_and_rates(monkeypatch):
    _patch_cents(monkeypatch)
    repo = InMemoryRepository()
    _mk_mission(repo)
    mission = repo.get_mission("m1")

    steps = [
        _done_step(1, "incorporation", "CR-RN-2026-104512"),
        _done_step(2, "land_eligibility", "CR-RN-2026-104513"),
    ]
    mcp = FakeMcp(
        engagement=_engagement("submitted", steps),
        registry={
            "CR-RN-2026-104512": (200, {"ref": "CR-RN-2026-104512", "kind": "incorporation"}),
            "CR-RN-2026-104513": (200, {"ref": "CR-RN-2026-104513", "kind": "land_eligibility"}),
        },
    )
    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.PUBSUB, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.RELEASED
    assert "approve_and_release_payment" in mcp.tools
    assert "reject_and_open_dispute" not in mcp.tools
    assert mcp.tools[-1] == "rate_provider"

    wake = repo.get_wakes("m1")[-1]
    assert wake.policy.verdict is Verdict.RELEASE
    assert wake.policy.failed_predicates == []
    assert len(wake.proof_checks) == 2
    assert all(c.verified and c.verify_error is None for c in wake.proof_checks)


# --------------------------------------------------------------------------- #
# Wake 2 — a 404 proof disputes and NEVER approves
# --------------------------------------------------------------------------- #
def test_wake2_bad_ref_disputes_never_approves(monkeypatch):
    _patch_cents(monkeypatch)
    repo = InMemoryRepository()
    _mk_mission(repo)
    mission = repo.get_mission("m1")

    steps = [
        _done_step(1, "incorporation", "CR-RN-2026-104512"),
        _done_step(2, "land_eligibility", "CR-RN-2026-999999"),  # 404
    ]
    mcp = FakeMcp(
        engagement=_engagement("submitted", steps),
        registry={"CR-RN-2026-104512": (200, {"ref": "CR-RN-2026-104512", "kind": "incorporation"})},
    )
    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.PUBSUB, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.DISPUTED
    assert "approve_and_release_payment" not in mcp.tools  # HARD RULE
    assert "reject_and_open_dispute" in mcp.tools

    wake = repo.get_wakes("m1")[-1]
    assert wake.policy.verdict is Verdict.DISPUTE
    assert "P2" in wake.policy.failed_predicates
    bad = next(c for c in wake.proof_checks if c.ref == "CR-RN-2026-999999")
    assert bad.verified is False and bad.verify_error is VerifyError.NOT_FOUND


def test_wake2_registry_502_is_unavailable_not_notfound(monkeypatch):
    _patch_cents(monkeypatch)
    repo = InMemoryRepository()
    _mk_mission(repo)
    mission = repo.get_mission("m1")

    steps = [_done_step(1, "incorporation", "CR-RN-2026-104512")]
    mcp = FakeMcp(
        engagement=_engagement("submitted", steps),
        registry={"CR-RN-2026-104512": (502, None)},  # registry unavailable
    )
    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.PUBSUB, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.DISPUTED
    assert "approve_and_release_payment" not in mcp.tools
    wake = repo.get_wakes("m1")[-1]
    assert "P2" in wake.policy.failed_predicates
    assert wake.proof_checks[0].verify_error is VerifyError.UNAVAILABLE


def test_wake2_kind_mismatch_disputes_never_approves(monkeypatch):
    _patch_cents(monkeypatch)
    repo = InMemoryRepository()
    _mk_mission(repo)
    mission = repo.get_mission("m1")

    steps = [_done_step(1, "incorporation", "CR-RN-2026-200001")]
    mcp = FakeMcp(
        engagement=_engagement("submitted", steps),
        registry={"CR-RN-2026-200001": (200, {"ref": "CR-RN-2026-200001", "kind": "permit"})},
    )
    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.PUBSUB, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.DISPUTED
    assert "approve_and_release_payment" not in mcp.tools
    assert "P3" in repo.get_wakes("m1")[-1].policy.failed_predicates


# --------------------------------------------------------------------------- #
# Wake 2 — pre-deadline sweep -> WAIT without running the gate
# --------------------------------------------------------------------------- #
def test_wake2_pre_deadline_sweep_waits_without_gate(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - asserts the branch is not taken
        raise AssertionError("policy.evaluate must not run on a WAIT")

    monkeypatch.setattr(agent.policy, "evaluate", explode)

    repo = InMemoryRepository()
    _mk_mission(repo, created_at=utcnow())  # deadline far in the future
    mission = repo.get_mission("m1")
    mcp = FakeMcp(engagement=_engagement("funded", [{"position": 1, "title": "x", "status": "pending"}]))

    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.SWEEP, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.AWAITING_DELIVERY  # unchanged
    assert "approve_and_release_payment" not in mcp.tools
    assert "reject_and_open_dispute" not in mcp.tools
    assert repo.get_wakes("m1")[-1].policy.verdict is Verdict.WAIT


def test_wake2_post_deadline_nondelivery_disputes(monkeypatch):
    _patch_cents(monkeypatch)
    repo = InMemoryRepository()
    _mk_mission(repo, created_at=utcnow() - timedelta(days=2))  # deadline elapsed
    mission = repo.get_mission("m1")
    mcp = FakeMcp(engagement=_engagement("funded", [{"position": 1, "title": "x", "status": "pending"}]))

    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.SWEEP, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.DISPUTED
    assert "approve_and_release_payment" not in mcp.tools
    wake = repo.get_wakes("m1")[-1]
    assert wake.policy.verdict is Verdict.DISPUTE
    assert "P1" in wake.policy.failed_predicates


# --------------------------------------------------------------------------- #
# Wake 2 — idempotency against at-least-once delivery
# --------------------------------------------------------------------------- #
def test_wake2_noop_when_already_settled():
    repo = InMemoryRepository()
    _mk_mission(repo)
    repo.update_status("m1", MissionStatus.VERIFYING)
    repo.update_status("m1", MissionStatus.RELEASED)
    mission = repo.get_mission("m1")

    mcp = FakeMcp(engagement=_engagement("completed", []))
    result = run(run_wake2(mission, repo, StubJudge(SETTINGS), WakeTrigger.PUBSUB, client=mcp, settings=SETTINGS))

    assert result.status is MissionStatus.RELEASED
    assert mcp.tools == []  # nothing was called
