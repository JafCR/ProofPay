"""FastAPI application and endpoints (SPEC §2.2).

Thin HTTP layer over :class:`proofpay.orchestrator.Orchestrator`, which runs the
two wake cycles. This module owns routing, the demo-token guard, delivery-event
parsing, sweep enumeration and the trace view; the wake logic lives in
``orchestrator.py``, the MCP calls in ``agent.py`` (``PactaMarketplace``), the
judgment calls in ``judge.py``, and the release gate in ``policy.py``.

| Method | Path              | Purpose                                        |
|--------|-------------------|------------------------------------------------|
| POST   | /missions         | Create a mission and run Wake 1 (synchronous)  |
| POST   | /events/delivery  | Pub/Sub push → Wake 2 for the delivered mission|
| POST   | /sweep            | Re-check missions stuck in AWAITING_DELIVERY   |
| GET    | /missions/{id}    | Full mission trace as JSON                      |
| GET    | /                 | Static one-page trace viewer                    |

Dependencies are stored on ``app.state``. :func:`create_app` accepts injected
instances so the endpoint tests run offline with a stub :class:`Marketplace` and
the deterministic ``StubJudge``. Production wiring builds the real
``PactaMarketplace`` (``agent.build_marketplace``) and judge (``get_judge`` →
``StubJudge`` unless ``JUDGE_STUB=0``) lazily, so importing this module never
requires the Google SDKs.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .events import EventParseError, parse_delivery_event
from .judge import Judge, get_judge
from .models import Mission, MissionStatus, MissionTrace, WakeTrigger
from .orchestrator import Marketplace, Orchestrator
from .settings import Settings, get_settings
from .state import (
    FirestoreRepository,
    InMemoryRepository,
    MissionNotFound,
    MissionRepository,
    StateError,
)


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class CreateMissionRequest(BaseModel):
    goal: str = Field(min_length=1)
    budget_usd: int = Field(ge=0)


class CompositeJob(BaseModel):
    goal: str = Field(min_length=1)
    budget_usd: int = Field(ge=0)


class CreateCompositeRequest(BaseModel):
    """A coordinated mission: one goal fulfilled by several independent hires.

    Each job becomes a normal child mission with its own engagement, escrow and
    wake cycles; the parent only aggregates them and completes when every child
    settles (its status is derived, never stored).
    """

    goal: str = Field(min_length=1)
    jobs: list[CompositeJob] = Field(min_length=2, max_length=4)


# --------------------------------------------------------------------------- #
# Dependency construction
# --------------------------------------------------------------------------- #
def _build_repository(settings: Settings) -> MissionRepository:
    # state_backend: "memory" and "firestore" force a store; "auto" (default)
    # keeps the historical rule - Firestore iff a GCP project is configured.
    # `memory` matters because the Vertex judge needs GOOGLE_CLOUD_PROJECT set,
    # which under `auto` would otherwise drag in Firestore for a local demo.
    backend = settings.state_backend
    if backend == "memory":
        return InMemoryRepository()
    if backend == "firestore" or (backend == "auto" and settings.google_cloud_project):
        return FirestoreRepository(
            project=settings.google_cloud_project or None,
            database=settings.firestore_database,
        )
    return InMemoryRepository()


def create_app(
    *,
    settings: Settings | None = None,
    repository: MissionRepository | None = None,
    marketplace: Marketplace | None = None,
    judge: Judge | None = None,
) -> FastAPI:
    """Build the app. Anything left ``None`` is constructed from settings; tests
    inject an ``InMemoryRepository``, a stub ``Marketplace`` and (optionally) a
    custom judge."""
    settings = settings or get_settings()
    repository = repository or _build_repository(settings)
    judge = judge or get_judge(settings)
    if marketplace is None:
        # Lazy: agent.build_marketplace constructs PactaMarketplace, whose MCP
        # transport imports `mcp` only when a tool is actually called.
        from .agent import build_marketplace

        marketplace = build_marketplace(settings)

    orchestrator = Orchestrator(repository, marketplace, judge, settings)

    app = FastAPI(title="ProofPay Agent", version="0.1.0")
    app.state.settings = settings
    app.state.repository = repository
    app.state.orchestrator = orchestrator

    _register_error_handlers(app)
    _register_routes(app)

    # The styled trace page (web/, SPEC §2.4) ships in the same container. Its
    # fixtures power the page's ?fixture= demo mode; both are optional so a bare
    # test app still works with the built-in fallback viewer.
    fixtures = _web_dir() / "fixtures"
    if fixtures.is_dir():
        app.mount("/fixtures", StaticFiles(directory=fixtures), name="fixtures")
    return app


def _web_dir() -> Path:
    """web/ lives at the repo root next to agent/; PROOFPAY_WEB_DIR overrides
    (the Docker image copies it elsewhere)."""
    default = Path(__file__).resolve().parents[3] / "web"
    return Path(os.environ.get("PROOFPAY_WEB_DIR", str(default)))


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _require_demo_token(
    request: Request, x_demo_token: str | None = Header(default=None)
) -> None:
    """Guard POST /missions with the demo token when one is configured. With no
    token set (local dev) the endpoint is open (SPEC §2.2 'demo token header')."""
    expected = request.app.state.settings.demo_token
    if not expected:
        return
    if x_demo_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing demo token")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def _register_routes(app: FastAPI) -> None:
    @app.post("/missions")
    async def create_mission(
        body: CreateMissionRequest,
        _: None = Depends(_require_demo_token),
    ) -> MissionTrace:
        repo: MissionRepository = app.state.repository
        orchestrator: Orchestrator = app.state.orchestrator
        mission = Mission(
            mission_id=uuid.uuid4().hex,
            goal=body.goal,
            budget_usd=body.budget_usd,
            status=MissionStatus.CREATED,
        )
        repo.create_mission(mission)
        await orchestrator.run_wake_one(mission.mission_id)
        return repo.get_trace(mission.mission_id)

    @app.post("/missions/composite")
    async def create_composite_mission(
        body: CreateCompositeRequest,
        _: None = Depends(_require_demo_token),
    ) -> MissionTrace:
        repo: MissionRepository = app.state.repository
        orchestrator: Orchestrator = app.state.orchestrator
        parent = Mission(
            mission_id=uuid.uuid4().hex,
            goal=body.goal,
            budget_usd=sum(job.budget_usd for job in body.jobs),
            status=MissionStatus.CREATED,
        )
        repo.create_mission(parent)
        child_ids: list[str] = []
        for job in body.jobs:
            child = Mission(
                mission_id=uuid.uuid4().hex,
                goal=job.goal,
                budget_usd=job.budget_usd,
                status=MissionStatus.CREATED,
                parent_id=parent.mission_id,
            )
            repo.create_mission(child)
            child_ids.append(child.mission_id)
            # Register the child on the parent before its Wake 1 so a failure
            # mid-hire still leaves the parent pointing at every child started.
            repo.patch_mission(parent.mission_id, child_ids=list(child_ids))
            await orchestrator.run_wake_one(child.mission_id)
        return _composite_trace(repo, repo.get_mission(parent.mission_id))

    @app.post("/events/delivery")
    async def delivery(request: Request) -> JSONResponse:
        repo: MissionRepository = app.state.repository
        orchestrator: Orchestrator = app.state.orchestrator
        body = await _json_body(request)
        event = parse_delivery_event(body)
        mission_id = _resolve_mission_id(repo, event)
        if mission_id is None:
            # Nothing matches this delivery (already cleaned up, or unknown
            # engagement). Ack so Pub/Sub does not redeliver forever.
            return JSONResponse(
                {"status": "ignored", "reason": "no matching mission"}
            )
        wake = await orchestrator.run_wake_two(mission_id, WakeTrigger.PUBSUB)
        mission = repo.get_mission(mission_id)
        return JSONResponse(
            {
                "status": "ok",
                "mission_id": mission_id,
                "mission_status": mission.status.value,
                "verdict": wake.policy.verdict.value if wake.policy else None,
            }
        )

    @app.post("/sweep")
    async def sweep() -> JSONResponse:
        repo: MissionRepository = app.state.repository
        orchestrator: Orchestrator = app.state.orchestrator
        pending = repo.list_missions(status=MissionStatus.AWAITING_DELIVERY)
        results = []
        for mission in pending:
            wake = await orchestrator.run_wake_two(
                mission.mission_id, WakeTrigger.SWEEP
            )
            results.append(
                {
                    "mission_id": mission.mission_id,
                    "mission_status": repo.get_mission(mission.mission_id).status.value,
                    "verdict": wake.policy.verdict.value if wake.policy else None,
                }
            )
        return JSONResponse({"status": "ok", "swept": len(results), "results": results})

    @app.get("/missions/{mission_id}")
    async def get_mission(mission_id: str) -> MissionTrace:
        repo: MissionRepository = app.state.repository
        mission = repo.get_mission(mission_id)
        if mission.child_ids:
            return _composite_trace(repo, mission)
        return repo.get_trace(mission_id)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        page = _web_dir() / "index.html"
        if page.is_file():
            return page.read_text(encoding="utf-8")
        return _TRACE_VIEWER_HTML


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MissionNotFound)
    async def _not_found(_: Request, exc: MissionNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(EventParseError)
    async def _bad_event(_: Request, exc: EventParseError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(StateError)
    async def _state_error(_: Request, exc: StateError) -> JSONResponse:
        # Illegal transitions and the like are conflicts, not server faults.
        return JSONResponse(status_code=409, content={"error": str(exc)})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception as exc:  # noqa: BLE001 - any malformed body is a 400
        raise EventParseError(f"request body is not valid JSON: {exc}") from exc


#: Progress order for deriving a composite parent's status from its children.
_STATUS_ORDER = [
    MissionStatus.CREATED,
    MissionStatus.CONTRACTED,
    MissionStatus.FUNDED,
    MissionStatus.AWAITING_DELIVERY,
    MissionStatus.VERIFYING,
]


def _composite_status(children: list[Mission]) -> MissionStatus:
    """Aggregate status of a composite parent, derived (never stored).

    Any disputed child disputes the whole coordination; it completes only when
    every child released; otherwise it sits at the least-advanced in-flight
    child's phase (a released child no longer holds the parent back)."""
    statuses = [c.status for c in children]
    if MissionStatus.DISPUTED in statuses:
        return MissionStatus.DISPUTED
    if statuses and all(s is MissionStatus.RELEASED for s in statuses):
        return MissionStatus.RELEASED
    in_flight = [s for s in statuses if s is not MissionStatus.RELEASED]
    return min(in_flight, key=_STATUS_ORDER.index, default=MissionStatus.CREATED)


def _composite_trace(repo: MissionRepository, parent: Mission) -> MissionTrace:
    children = [repo.get_trace(cid) for cid in parent.child_ids]
    derived = _composite_status([t.mission for t in children])
    return MissionTrace(
        mission=parent.model_copy(update={"status": derived}),
        wakes=[],
        children=children,
    )


def _resolve_mission_id(repo: MissionRepository, event) -> str | None:
    if event.mission_id:
        try:
            repo.get_mission(event.mission_id)
            return event.mission_id
        except MissionNotFound:
            return None
    # Resolve by engagement id (the event may carry only that). Match only
    # missions still awaiting delivery: after a marketplace reset the engagement
    # ids restart from 1, so a settled mission can share an id with a new one.
    for mission in repo.list_missions(status=MissionStatus.AWAITING_DELIVERY):
        if mission.engagement_id and mission.engagement_id == event.engagement_id:
            return mission.mission_id
    return None


# Minimal built-in trace viewer. web/ (task #8) is the styled Pacta-palette page;
# this keeps GET / working from a bare agent container / in tests.
_TRACE_VIEWER_HTML = """<!doctype html>
<meta charset="utf-8">
<title>ProofPay - mission trace</title>
<style>
  body { font: 14px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 780px;
         background:#0A0E17; color:#e6ecff; }
  input,button{font:inherit;padding:.4rem .6rem;border-radius:6px;border:1px solid #4353FF;}
  input{background:#111830;color:#e6ecff;width:22rem;}
  button{background:#4353FF;color:#fff;cursor:pointer;}
  pre{background:#111830;padding:1rem;border-radius:8px;overflow:auto;}
</style>
<h1>ProofPay mission trace</h1>
<p>Enter a mission id to fetch its trace from <code>/missions/{id}</code>.</p>
<input id="mid" placeholder="mission id">
<button onclick="load()">Load</button>
<pre id="out">-</pre>
<script>
async function load(){
  const id = document.getElementById('mid').value.trim();
  if(!id) return;
  const r = await fetch('/missions/'+encodeURIComponent(id));
  document.getElementById('out').textContent =
    JSON.stringify(await r.json(), null, 2);
}
</script>
"""


# Run in production with uvicorn's factory mode so the app is built at startup
# (which builds the real marketplace/judge), not at import:
#     uvicorn proofpay.main:create_app --factory --host 0.0.0.0 --port $PORT

__all__ = ["create_app", "CreateMissionRequest", "CreateCompositeRequest"]
