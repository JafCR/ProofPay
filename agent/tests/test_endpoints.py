"""Endpoint + orchestration tests (SPEC §2.2), fully offline.

A stub :class:`~proofpay.orchestrator.Marketplace` and the real deterministic
``StubJudge`` drive the two wakes through the actual FastAPI app + Orchestrator +
policy gate - no network, no MCP, no Google SDK. The load-bearing assertion in
every dispute test: ``approve_and_release_payment`` is reached only on a policy
RELEASE, never otherwise (docs/SPEC.md §3).
"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from proofpay.judge import ProofAssessment, StubJudge
from proofpay.main import create_app
from proofpay.models import MissionStatus
from proofpay.orchestrator import RegistryUnavailable
from proofpay.settings import Settings
from proofpay.state import InMemoryRepository

# --------------------------------------------------------------------------- #
# Offers in Pacta's MCP summary shape (CONTRACTS.md §3): nested provider, money as
# "$…" strings - exactly what judge.StubJudge parses and ranks.
# --------------------------------------------------------------------------- #
ANCHORED_OFFER = {
    "offer_id": 1,
    "title": "Establish a Costa Rican company able to buy land and operate a hotel",
    "price": "$5,000",
    "escrow_terms": "20% downpayment, 80% on completion",
    "steps": [
        {"position": 1, "title": "Incorporate S.R.L.", "requires_registry_proof": "incorporation"},
        {"position": 2, "title": "Land eligibility", "requires_registry_proof": "land_eligibility"},
    ],
    "provider": {
        "smb_id": 1, "name": "Bufete Herrera & Asociados", "location": "Costa Rica",
        "category": "legal", "vetted": True, "collateral_at_stake": "$1,500",
        "rating": "3 good / 1 bad (score 2)",
    },
    "_price_cents": 500000, "_upfront_pct": 20,  # internal hints; judge ignores them
}
CHEAP_OFFER = {
    "offer_id": 2,
    "title": "Budget formation",
    "price": "$900",
    "escrow_terms": "50% downpayment, 50% on completion",
    "steps": [{"position": 1, "title": "Incorporate"}],
    "provider": {
        "smb_id": 2, "name": "LexCorp", "location": "Costa Rica", "category": "legal",
        "vetted": True, "collateral_at_stake": "$1,000", "rating": "2 good / 0 bad (score 2)",
    },
    "_price_cents": 90000, "_upfront_pct": 50,
}

REGISTRY = {
    "CR-RN-2026-104512": {"ref": "CR-RN-2026-104512", "kind": "incorporation", "title": "SRL cert"},
    "CR-RN-2026-104513": {"ref": "CR-RN-2026-104513", "kind": "land_eligibility", "title": "Eligibility"},
}


class VetoJudge(StubJudge):
    """Real StubJudge for select_offer/draft_dispute, but vetoes every proof -
    exercises the P4 (LLM veto) path even when the registry verifies."""

    def assess_proof(self, step_requirement, registry_record):
        return ProofAssessment(satisfies=False, reason="not convinced by the record")


class StubMarketplace:
    """In-memory Marketplace implementing the orchestrator's protocol."""

    def __init__(self, offers=None, registry=None, unavailable=None) -> None:
        self.offers = offers if offers is not None else [ANCHORED_OFFER, CHEAP_OFFER]
        self.registry = registry if registry is not None else dict(REGISTRY)
        self.unavailable = set(unavailable or [])
        self.engagement: dict | None = None
        self.calls: list[str] = []

    async def search_offers(self, query):
        self.calls.append("search_offers")
        return list(self.offers)

    async def create_engagement(self, offer_id):
        self.calls.append("create_engagement")
        offer = next(o for o in self.offers if str(o["offer_id"]) == str(offer_id))
        price = offer["_price_cents"]
        self.engagement = {
            "engagement_id": "eng-1",
            "state": "draft",
            "price_cents": price,
            "upfront_cents": price * offer["_upfront_pct"] // 100,
            "escrow_balance_cents": 0,
            "provider_name": offer["provider"]["name"],
            "steps": [
                {
                    "step_id": f"s{s['position']}",
                    "position": s["position"],
                    "title": s["title"],
                    "required_kind": s.get("requires_registry_proof") or "",
                    "proof_text": None,
                    "registry_ref": None,
                    "verified_by_platform": False,
                    "status": "pending",
                }
                for s in offer["steps"]
            ],
        }
        return self.engagement

    async def agree_to_contract(self, engagement_id):
        self.calls.append("agree_to_contract")
        self.engagement["state"] = "agreed"
        return self.engagement

    async def fund_escrow(self, engagement_id):
        self.calls.append("fund_escrow")
        self.engagement["state"] = "funded"
        self.engagement["escrow_balance_cents"] = self.engagement["upfront_cents"]
        return self.engagement

    async def get_engagement(self, engagement_id):
        self.calls.append("get_engagement")
        return self.engagement

    async def verify_registry_reference(self, ref):
        self.calls.append(f"verify:{ref}")
        if ref in self.unavailable:
            raise RegistryUnavailable(f"registry down for {ref}")
        return self.registry.get(ref)

    async def approve_and_release_payment(self, engagement_id):
        self.calls.append("approve_and_release_payment")
        self.engagement["state"] = "completed"
        self.engagement["escrow_balance_cents"] = 0
        return self.engagement

    async def reject_and_open_dispute(self, engagement_id, reason):
        self.calls.append("reject_and_open_dispute")
        self.engagement["state"] = "disputed"
        self.engagement["dispute_reason"] = reason
        return self.engagement

    async def rate_provider(self, engagement_id, value):
        self.calls.append(f"rate:{value}")
        return self.engagement

    # test helper: mark the engagement delivered (as the provider would)
    def deliver(self, *, refs: dict[str, str]) -> None:
        for step in self.engagement["steps"]:
            ref = refs.get(step["step_id"])
            step["status"] = "done"
            step["proof_text"] = f"done {step['step_id']}"
            step["registry_ref"] = ref
            step["verified_by_platform"] = ref is not None
        self.engagement["state"] = "submitted"


def make_client(marketplace=None, judge=None, settings=None):
    st = settings or Settings()
    mp = marketplace or StubMarketplace()
    jd = judge or StubJudge(st)
    app = create_app(settings=st, repository=InMemoryRepository(), marketplace=mp, judge=jd)
    return TestClient(app), mp


def _create(client) -> str:
    return client.post("/missions", json={"goal": "g", "budget_usd": 6000}).json()["mission"]["mission_id"]


# --------------------------------------------------------------------------- #
# Wake 1
# --------------------------------------------------------------------------- #
def test_create_mission_runs_wake_one_and_sleeps():
    client, mp = make_client()
    r = client.post("/missions", json={"goal": "form a company in Costa Rica", "budget_usd": 6000})
    assert r.status_code == 200
    mission = r.json()["mission"]
    assert mission["status"] == MissionStatus.AWAITING_DELIVERY.value
    # Picked the higher-collateral anchored offer and funded escrow.
    assert mission["offer_id"] == "1"
    assert mission["provider_name"] == "Bufete Herrera & Asociados"
    assert mission["engagement_id"] == "eng-1"
    assert mission["selection"]["offer_id"] == "1"
    assert "approve_and_release_payment" not in mp.calls
    assert {"search_offers", "create_engagement", "agree_to_contract", "fund_escrow"} <= set(mp.calls)


def test_create_mission_validates_body():
    client, _ = make_client()
    assert client.post("/missions", json={"goal": ""}).status_code == 422
    assert client.post("/missions", json={"budget_usd": 5}).status_code == 422


# --------------------------------------------------------------------------- #
# Wake 2 - happy path
# --------------------------------------------------------------------------- #
def test_delivery_happy_path_releases():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})

    r = client.post("/events/delivery", json={"engagement_id": "eng-1", "mission_id": mid})
    assert r.status_code == 200
    assert r.json()["verdict"] == "RELEASE"

    trace = client.get(f"/missions/{mid}").json()
    assert trace["mission"]["status"] == MissionStatus.RELEASED.value
    assert "approve_and_release_payment" in mp.calls
    assert "rate:good" in mp.calls
    assert "reject_and_open_dispute" not in mp.calls
    wake2 = trace["wakes"][-1]
    assert wake2["policy"]["verdict"] == "RELEASE"
    assert wake2["policy"]["failed_predicates"] == []


# --------------------------------------------------------------------------- #
# Wake 2 - dispute paths: the release must NOT fire
# --------------------------------------------------------------------------- #
def test_delivery_unverifiable_reference_disputes_and_never_releases():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-999999", "s2": "CR-RN-2026-104513"})  # 999999 not in registry

    r = client.post("/events/delivery", json={"mission_id": mid})
    assert r.json()["verdict"] == "DISPUTE"

    trace = client.get(f"/missions/{mid}").json()
    assert trace["mission"]["status"] == MissionStatus.DISPUTED.value
    assert "approve_and_release_payment" not in mp.calls  # the load-bearing assertion
    assert "reject_and_open_dispute" in mp.calls
    assert "P2" in trace["wakes"][-1]["policy"]["failed_predicates"]


def test_llm_veto_disputes_even_when_registry_verifies():
    client, mp = make_client(judge=VetoJudge(Settings()))
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})

    r = client.post("/events/delivery", json={"mission_id": mid})
    assert r.json()["verdict"] == "DISPUTE"
    assert "approve_and_release_payment" not in mp.calls
    trace = client.get(f"/missions/{mid}").json()
    assert "P4" in trace["wakes"][-1]["policy"]["failed_predicates"]


def test_registry_unavailable_disputes_conservatively():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})
    mp.unavailable.add("CR-RN-2026-104512")

    r = client.post("/events/delivery", json={"mission_id": mid})
    assert r.json()["verdict"] == "DISPUTE"
    assert "approve_and_release_payment" not in mp.calls


# --------------------------------------------------------------------------- #
# Sweep + WAIT + non-delivery dispute
# --------------------------------------------------------------------------- #
def test_sweep_before_delivery_waits_and_stays_awaiting():
    client, mp = make_client(settings=Settings(delivery_deadline_seconds=86400))
    mid = _create(client)  # no delivery yet
    body = client.post("/sweep").json()
    assert body["swept"] == 1
    assert body["results"][0]["verdict"] == "WAIT"
    trace = client.get(f"/missions/{mid}").json()
    assert trace["mission"]["status"] == MissionStatus.AWAITING_DELIVERY.value
    assert "approve_and_release_payment" not in mp.calls
    # WAIT keeps the mission awaiting, so a second sweep still sees it.
    assert client.post("/sweep").json()["swept"] == 1


def test_sweep_after_deadline_without_delivery_disputes():
    client, mp = make_client(settings=Settings(delivery_deadline_seconds=0))
    mid = _create(client)
    r = client.post("/sweep")
    assert r.json()["results"][0]["verdict"] == "DISPUTE"
    trace = client.get(f"/missions/{mid}").json()
    assert trace["mission"]["status"] == MissionStatus.DISPUTED.value
    assert "P1" in trace["wakes"][-1]["policy"]["failed_predicates"]
    assert "approve_and_release_payment" not in mp.calls


def test_delivery_is_idempotent_after_settlement():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})
    client.post("/events/delivery", json={"mission_id": mid})
    releases_before = mp.calls.count("approve_and_release_payment")
    wakes_before = len(client.get(f"/missions/{mid}").json()["wakes"])
    # A duplicate delivery must not release again or add a settling wake.
    r = client.post("/events/delivery", json={"mission_id": mid})
    assert r.status_code == 200
    assert mp.calls.count("approve_and_release_payment") == releases_before
    assert len(client.get(f"/missions/{mid}").json()["wakes"]) == wakes_before


# --------------------------------------------------------------------------- #
# Delivery event parsing + resolution + misc endpoints
# --------------------------------------------------------------------------- #
def test_delivery_pubsub_envelope_is_parsed():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})
    data = base64.b64encode(json.dumps({"mission_id": mid}).encode()).decode()
    r = client.post("/events/delivery", json={"message": {"data": data, "messageId": "m1"}})
    assert r.status_code == 200
    assert r.json()["mission_id"] == mid


def test_delivery_resolves_mission_by_engagement_id():
    client, mp = make_client()
    mid = _create(client)
    mp.deliver(refs={"s1": "CR-RN-2026-104512", "s2": "CR-RN-2026-104513"})
    r = client.post("/events/delivery", json={"engagement_id": "eng-1"})  # no mission_id
    assert r.json()["mission_id"] == mid


def test_delivery_with_unmatched_engagement_is_ignored():
    client, _ = make_client()
    r = client.post("/events/delivery", json={"engagement_id": "does-not-exist"})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_delivery_bad_event_is_400():
    client, _ = make_client()
    assert client.post("/events/delivery", json={"foo": "bar"}).status_code == 400


def test_get_unknown_mission_is_404():
    client, _ = make_client()
    assert client.get("/missions/nope").status_code == 404


def test_index_serves_trace_viewer():
    client, _ = make_client()
    r = client.get("/")
    assert r.status_code == 200
    assert "ProofPay" in r.text


def test_demo_token_enforced_when_configured():
    client, _ = make_client(settings=Settings(demo_token="s3cret"))
    assert client.post("/missions", json={"goal": "g", "budget_usd": 6000}).status_code == 401
    ok = client.post(
        "/missions",
        json={"goal": "g", "budget_usd": 6000},
        headers={"X-Demo-Token": "s3cret"},
    )
    assert ok.status_code == 200
