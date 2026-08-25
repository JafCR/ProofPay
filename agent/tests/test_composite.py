"""Composite (coordinated) missions and the offers_considered trace snapshot.

A composite mission is one goal fulfilled by several independent hires: each job
runs as a normal child mission with its own engagement and wakes; the parent
aggregates and its status is derived at read time, never stored. These tests
drive the real FastAPI app with a multi-engagement stub marketplace, offline.
"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from proofpay.judge import StubJudge
from proofpay.main import create_app
from proofpay.models import MissionStatus
from proofpay.settings import Settings
from proofpay.state import InMemoryRepository

from .test_endpoints import ANCHORED_OFFER, CHEAP_OFFER, REGISTRY, StubMarketplace

# A tourism-style offer with free-text proofs only (no registry anchoring),
# mirroring Pacta's seeded eco-tour offer: P2-P4 pass vacuously, P1+P5 decide.
TOUR_OFFER = {
    "offer_id": 3,
    "title": "Design and book a 7-day eco-tour itinerary",
    "price": "$1,200",
    "escrow_terms": "50% downpayment, 50% on completion",
    "steps": [
        {"position": 1, "title": "Draft itinerary"},
        {"position": 2, "title": "Confirm bookings"},
    ],
    "provider": {
        "smb_id": 3, "name": "Tico Adventures Tours", "location": "Costa Rica",
        "category": "tourism", "vetted": True, "collateral_at_stake": "$500",
        "rating": "4 good / 0 bad (score 4)",
    },
    "_price_cents": 120000, "_upfront_pct": 50,
}


class MultiMarketplace(StubMarketplace):
    """StubMarketplace that keeps one engagement per hire (composite needs 2+)."""

    def __init__(self, offers=None, registry=None) -> None:
        super().__init__(offers=offers, registry=registry)
        self.engagements: dict[str, dict] = {}

    async def create_engagement(self, offer_id):
        await super().create_engagement(offer_id)
        engagement_id = f"eng-{len(self.engagements) + 1}"
        self.engagement["engagement_id"] = engagement_id
        self.engagements[engagement_id] = self.engagement
        return self.engagement

    async def agree_to_contract(self, engagement_id):
        self.engagements[engagement_id]["state"] = "agreed"
        return self.engagements[engagement_id]

    async def fund_escrow(self, engagement_id):
        e = self.engagements[engagement_id]
        e["state"] = "funded"
        e["escrow_balance_cents"] = e["upfront_cents"]
        return e

    async def get_engagement(self, engagement_id):
        return self.engagements[engagement_id]

    async def approve_and_release_payment(self, engagement_id):
        self.calls.append("approve_and_release_payment")
        e = self.engagements[engagement_id]
        e["state"] = "completed"
        e["escrow_balance_cents"] = 0
        return e

    async def reject_and_open_dispute(self, engagement_id, reason):
        self.calls.append("reject_and_open_dispute")
        e = self.engagements[engagement_id]
        e["state"] = "disputed"
        e["dispute_reason"] = reason
        return e

    def deliver_engagement(self, engagement_id, *, refs: dict[str, str]) -> None:
        e = self.engagements[engagement_id]
        for step in e["steps"]:
            ref = refs.get(step["step_id"])
            step["status"] = "done"
            step["proof_text"] = f"done {step['step_id']}"
            step["registry_ref"] = ref
            step["verified_by_platform"] = ref is not None
        e["state"] = "submitted"


ALL_OFFERS = [ANCHORED_OFFER, CHEAP_OFFER, TOUR_OFFER]

COMPOSITE_BODY = {
    "goal": "Open a boutique hotel: form the company and scout the site",
    "jobs": [
        {"goal": "form a company in Costa Rica", "budget_usd": 6000},
        {"goal": "book an eco-tour to scout locations", "budget_usd": 1300},
    ],
}


def make_client():
    st = Settings()
    mp = MultiMarketplace(offers=list(ALL_OFFERS))
    app = create_app(
        settings=st,
        repository=InMemoryRepository(),
        marketplace=mp,
        judge=StubJudge(st),
    )
    return TestClient(app), mp


def _deliver_event(client, engagement_id):
    payload = base64.b64encode(json.dumps({"engagement_id": engagement_id}).encode())
    return client.post(
        "/events/delivery", json={"message": {"data": payload.decode()}}
    )


# --------------------------------------------------------------------------- #
# offers_considered snapshot (single missions)
# --------------------------------------------------------------------------- #
def test_mission_records_offer_cards():
    client, _ = make_client()
    r = client.post("/missions", json={"goal": "g", "budget_usd": 6000})
    cards = r.json()["mission"]["offers_considered"]
    assert len(cards) == len(ALL_OFFERS)
    by_id = {c["offer_id"]: c for c in cards}
    assert by_id["1"]["chosen"] is True
    assert by_id["1"]["price_usd"] == 5000
    assert by_id["1"]["collateral_usd"] == 1500
    assert by_id["1"]["vetted"] is True
    assert by_id["1"]["provider_name"] == "Bufete Herrera & Asociados"
    assert by_id["3"]["chosen"] is False
    assert by_id["3"]["price_usd"] == 1200


# --------------------------------------------------------------------------- #
# Composite creation
# --------------------------------------------------------------------------- #
def test_composite_creates_children_and_awaits():
    client, mp = make_client()
    r = client.post("/missions/composite", json=COMPOSITE_BODY)
    assert r.status_code == 200
    trace = r.json()
    assert trace["mission"]["status"] == MissionStatus.AWAITING_DELIVERY.value
    assert trace["mission"]["budget_usd"] == 7300
    assert len(trace["children"]) == 2
    child_engagements = {
        c["mission"]["engagement_id"] for c in trace["children"]
    }
    assert child_engagements == {"eng-1", "eng-2"}
    for child in trace["children"]:
        assert child["mission"]["status"] == MissionStatus.AWAITING_DELIVERY.value
        assert child["mission"]["parent_id"] == trace["mission"]["mission_id"]
    # Budget steering: the second (small-budget) job cannot hire the $5,000 firm.
    goals_to_offer = {
        c["mission"]["goal"]: c["mission"]["offer_id"] for c in trace["children"]
    }
    assert goals_to_offer["book an eco-tour to scout locations"] != "1"
    assert "approve_and_release_payment" not in mp.calls


def test_composite_validates_body():
    client, _ = make_client()
    r = client.post(
        "/missions/composite",
        json={"goal": "g", "jobs": [{"goal": "one", "budget_usd": 10}]},
    )
    assert r.status_code == 422  # needs at least two jobs


# --------------------------------------------------------------------------- #
# Composite settlement - derived status
# --------------------------------------------------------------------------- #
def _create_composite(client):
    trace = client.post("/missions/composite", json=COMPOSITE_BODY).json()
    parent_id = trace["mission"]["mission_id"]
    engagement_ids = [c["mission"]["engagement_id"] for c in trace["children"]]
    return parent_id, engagement_ids


def _deliver_honest(mp, engagement_id):
    e = mp.engagements[engagement_id]
    refs = {}
    for step in e["steps"]:
        if step["required_kind"]:
            match = [r for r, rec in REGISTRY.items() if rec["kind"] == step["required_kind"]]
            refs[step["step_id"]] = match[0]
    mp.deliver_engagement(engagement_id, refs=refs)


def test_composite_stays_awaiting_until_all_children_settle():
    client, mp = make_client()
    parent_id, engagements = _create_composite(client)
    _deliver_honest(mp, engagements[0])
    assert _deliver_event(client, engagements[0]).status_code == 200
    trace = client.get(f"/missions/{parent_id}").json()
    statuses = sorted(c["mission"]["status"] for c in trace["children"])
    assert statuses == ["AWAITING_DELIVERY", "RELEASED"]
    assert trace["mission"]["status"] == MissionStatus.AWAITING_DELIVERY.value


def test_composite_releases_when_every_child_releases():
    client, mp = make_client()
    parent_id, engagements = _create_composite(client)
    for engagement_id in engagements:
        _deliver_honest(mp, engagement_id)
        assert _deliver_event(client, engagement_id).status_code == 200
    trace = client.get(f"/missions/{parent_id}").json()
    assert trace["mission"]["status"] == MissionStatus.RELEASED.value
    assert all(
        c["mission"]["status"] == MissionStatus.RELEASED.value
        for c in trace["children"]
    )


def test_composite_disputes_if_any_child_disputes():
    client, mp = make_client()
    parent_id, engagements = _create_composite(client)
    _deliver_honest(mp, engagements[0])
    # Sabotage the first child's delivery: drop one registry record so the
    # agent's own re-verification 404s (P2) and that child disputes.
    e = mp.engagements[engagements[0]]
    anchored = next(s for s in e["steps"] if s["required_kind"])
    mp.registry.pop(anchored["registry_ref"])
    assert _deliver_event(client, engagements[0]).status_code == 200
    trace = client.get(f"/missions/{parent_id}").json()
    assert trace["mission"]["status"] == MissionStatus.DISPUTED.value
