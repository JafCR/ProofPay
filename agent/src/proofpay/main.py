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

import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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


# --------------------------------------------------------------------------- #
# Dependency construction
# --------------------------------------------------------------------------- #
def _build_repository(settings: Settings) -> MissionRepository:
    if settings.google_cloud_project:
        return FirestoreRepository(
            project=settings.google_cloud_project,
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
    return app


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
        return repo.get_trace(mission_id)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
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


def _resolve_mission_id(repo: MissionRepository, event) -> str | None:
    if event.mission_id:
        try:
            repo.get_mission(event.mission_id)
            return event.mission_id
        except MissionNotFound:
            return None
    # Resolve by engagement id (the event may carry only that).
    for mission in repo.list_missions():
        if mission.engagement_id and mission.engagement_id == event.engagement_id:
            return mission.mission_id
    return None


# Minimal built-in trace viewer. web/ (task #8) is the styled Pacta-palette page;
# this keeps GET / working from a bare agent container / in tests.
_TRACE_VIEWER_HTML = """<!doctype html>
<meta charset="utf-8">
<title>ProofPay — mission trace</title>
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
<pre id="out">—</pre>
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

__all__ = ["create_app", "CreateMissionRequest"]
