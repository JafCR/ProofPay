# ProofPay · Technical Specification v1.0

Target: All Things Agentic Hackathon (Google/Devpost), Taskmaster category.
This document is the single source of truth for the build. Hand it to the coding agent as-is.

---

## 0. One-paragraph summary

ProofPay is an autonomous procurement agent. Given a natural-language goal, it discovers a real, collateral-backed provider on a Pacta Protocol marketplace, locks an immutable contract, funds escrow, then terminates. Days later, a delivery event wakes it; it re-verifies every registry-anchored proof against the source, and either releases the payment or opens a dispute. The LLM (Gemini 3.5 Flash) makes judgment calls; a deterministic policy gate in code decides whether money is allowed to move. The LLM has no direct path to the release call.

## 1. Hard requirements (from contest rules, non-negotiable)

- R1. Gemini 3.5 Flash (or newer) accessed through the Gemini API or Vertex AI, used at runtime.
- R2. Google ADK as the agent framework. The google-genai SDK is also used for structured judgment calls.
- R3. Google Cloud infrastructure: Cloud Run (services), Firestore (state), Pub/Sub (delivery events), Cloud Scheduler (sweeps).
- R4. All new code written during the submission period. Pacta Protocol (MIT, pre-existing) is consumed as a dependency and disclosed in the README.
- R5. English README with reproducible spin-up instructions and an architecture diagram (mermaid source + rendered PNG committed).
- R6. A demo must be runnable end to end with two scripted scenarios: happy path and fraud path.

## 2. System components

Four deployables, one repo.

```
proofpay/
  README.md                  # spin-up, architecture diagram, disclosure note
  docs/architecture.mmd      # mermaid source
  docs/architecture.png
  marketplace/               # deployment wrapper for Pacta marketplace (pre-existing dep)
    Dockerfile               # node:22, clones/vendors Pacta.Protocol, runs start:pacta
    deploy.sh
  agent/                     # THE PROJECT. Python 3.11, ADK, FastAPI
    Dockerfile               # python 3.11 + node 22 (node needed for MCP stdio sidecar)
    pyproject.toml
    src/proofpay/
      main.py                # FastAPI app + endpoints
      agent.py               # ADK agent + MCPToolset wiring
      judge.py               # google-genai structured-output judgment calls
      policy.py              # deterministic release gate (pure functions, no I/O)
      state.py               # Firestore repository
      models.py              # pydantic models: Mission, WakeCycle, ProofCheck, Decision
      events.py              # Pub/Sub push handler parsing
      settings.py            # env config
    tests/
      test_policy.py         # exhaustive unit tests, no network
      test_state_machine.py
      test_judge_schemas.py  # validates JSON schemas, mocked model
    deploy.sh
  provider-bot/              # simulated SMB provider. Node or Python, small
    src/bot.js               # polls its engagements, delivers after DELAY_SECONDS
    Dockerfile
    deploy.sh
  web/                       # minimal mission trace viewer (single static page + JSON API)
  scripts/
    demo_happy.sh            # end-to-end happy path
    demo_fraud.sh            # end-to-end fraud path
    seed.sh                  # reset marketplace to seed state
  Makefile                   # make deploy-all / demo-happy / demo-fraud / test
```

### 2.1 marketplace (Cloud Run service `pacta-marketplace`)

- Runs the existing Pacta marketplace (`npm run start:pacta`, listens on 3220; map to $PORT).
- min-instances=1 during demo recording (engagement state is in-process for the demo marketplace), scale settings documented.
- No code changes to Pacta. If a change is unavoidable, it goes in Pacta upstream, not vendored forks.

### 2.2 agent (Cloud Run service `proofpay-agent`)

FastAPI app. Endpoints:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /missions | demo token header | Create mission from `{goal: str, budget_usd: int}` and run Wake 1 |
| POST | /events/delivery | Pub/Sub OIDC push | Delivery event, runs Wake 2 |
| POST | /sweep | Cloud Scheduler OIDC | Every 10 min, re-checks missions stuck in AWAITING_DELIVERY (fallback if event lost) |
| GET | /missions/{id} | public read | Full mission trace as JSON (decision log included) |
| GET | / | public | Static one-page trace viewer (fetches the JSON) |

**Wake 1 (synchronous, triggered by POST /missions):**
1. `search_offers(query)` via MCP.
2. `judge.select_offer(goal, offers)` → Gemini structured output `{offer_id, rationale, rejected: [{offer_id, reason}]}`. Instruction: zero-collateral providers are a different risk class; collateral and rating outrank price.
3. `create_engagement`, `agree_to_contract`, `fund_escrow` (mechanical, state machine, no LLM).
4. Persist mission → `AWAITING_DELIVERY`. Process exits (returns 200). Nothing lives in memory.

**Wake 2 (triggered by Pub/Sub push or sweep):**
1. Load mission from Firestore, assert state `AWAITING_DELIVERY`, create `wake_id`.
2. `get_engagement` → list of steps + submitted proofs.
3. For each proof: `verify_registry_reference(ref)` via MCP → store raw registry record (or error) in Firestore under this `wake_id`.
4. For each verified record: `judge.assess_proof(step_requirement, registry_record)` → `{satisfies: bool, reason}` (advisory).
5. `policy.evaluate(mission, checks)` → RELEASE or DISPUTE (see §3).
6. If RELEASE: `approve_and_release_payment`, then `rate_provider("good")`, state `RELEASED`.
   If DISPUTE: `judge.draft_dispute(mismatches)` → reason text, `reject_and_open_dispute(reason)`, state `DISPUTED`.
7. Append full decision log entry. Process exits.

**MCP wiring:** ADK `MCPToolset` with `StdioServerParameters`, spawning Pacta's `mcp/server.js` inside the same container (image ships node 22). Env: `MARKETPLACE_URL` = marketplace Cloud Run URL, `AGENT_ID` from settings. Do NOT reimplement the tools over REST; consuming the unmodified MCP server is the point of the project.

**ADK agent config:** model `gemini-3.5-flash` (configurable via `GEMINI_MODEL` env var; verify exact model string against current Gemini API model list at build time and pin it). Root agent instruction embeds the critical rule from Pacta docs: never call `approve_and_release_payment` before `verify_registry_reference` has been called on every registry-anchored proof; on any failure call `reject_and_open_dispute` with a reason. Note that even though the instruction says this, enforcement does not rely on it (§3).

### 2.3 provider-bot (Cloud Run service `proofpay-provider`, min-instances=0)

Simulates the SMB. Loop driven by Cloud Scheduler hit or simple internal timer while processing:
- Polls the marketplace REST API for engagements assigned to its provider in state agreed+funded.
- After `DELAY_SECONDS` (env; 90 for video, up to days for the long-run claim), submits fulfillment for each step.
- `MODE=honest`: submits the seeded valid registry references.
- `MODE=fraud`: submits one plausible but nonexistent reference (e.g. `CR-RN-2026-999999`) for one step.
- After submitting, publishes `{engagement_id, mission_id}` to Pub/Sub topic `proofpay-delivery`.

### 2.4 web (served from the agent container at /)

One static HTML page. Input: mission id. Renders the trace: state timeline, each wake, each proof check (required kind vs returned kind, verified true/false), the policy verdict, and the LLM rationales. Dark theme, Pacta palette (#0A0E17 / #4353FF / #58E0FF). No framework, vanilla JS, fetch the JSON endpoint. This is the "hosted project URL" for judges.

## 3. The policy gate (the load-bearing module)

`policy.py` is pure, synchronous, fully unit-tested, and the ONLY code path that can trigger `approve_and_release_payment`.

Release iff ALL hold, evaluated over data the agent fetched itself in the CURRENT wake:
- P1. Every fulfillment step has at least one submitted proof.
- P2. Every registry-anchored proof was passed to `verify_registry_reference` in this wake and returned a record (no error).
- P3. For every proof, `record.kind == step.required_kind`.
- P4. Every `judge.assess_proof` verdict is `satisfies=true`. (The LLM can veto a release; it can never force one. An LLM "true" with a failed P2/P3 still disputes.)
- P5. Escrow balance covers the release per `get_engagement`.

Anything else → DISPUTE, with the failing predicate ids recorded in the decision log. There is no third outcome and no retry-until-release loop.

Threat note for the README: proof text is attacker-controlled input. It reaches the model as untrusted data inside delimited context, never as instructions, and no model output is executed or interpolated into tool parameters except the human-readable dispute reason string.

## 4. Data model (Firestore)

Collection `missions/{mission_id}`:
```
goal, budget_usd, status, offer_id, engagement_id, provider_name,
created_at, updated_at,
selection: {offer_id, rationale, rejected[]}
```
Subcollection `missions/{id}/wakes/{wake_id}`:
```
trigger: "create" | "pubsub" | "sweep",
started_at, finished_at,
proof_checks: [{step_id, required_kind, ref, verified, returned_kind, llm_satisfies, llm_reason}],
policy: {verdict: "RELEASE"|"DISPUTE"|"WAIT", failed_predicates: []},
actions: [tool calls made],
model: {name, tokens_in, tokens_out}
```

Status enum: `CREATED → CONTRACTED → FUNDED → AWAITING_DELIVERY → VERIFYING → RELEASED | DISPUTED`. Transitions validated in `state.py`; illegal transitions raise.

## 5. Configuration

Env vars (all read in `settings.py`, no literals in code):
`GOOGLE_CLOUD_PROJECT, GEMINI_MODEL=gemini-3.5-flash, MARKETPLACE_URL, AGENT_ID=1, PUBSUB_TOPIC=proofpay-delivery, DEMO_TOKEN, FIRESTORE_DATABASE=(default)`.
Gemini API key via Secret Manager mounted as `GEMINI_API_KEY` (or ADC if Vertex path chosen; pick ONE and document it).

## 6. Demo scripts (must work with a single command each)

`make demo-happy`:
1. `seed.sh` resets marketplace, provider-bot MODE=honest DELAY_SECONDS=90.
2. POST /missions with the LandBridge-style goal ("Form a company in Costa Rica to purchase land in Guanacaste and operate a hotel. Budget $6,000.").
3. Poll GET /missions/{id} until RELEASED. Print the trace URL.

`make demo-fraud`: same, MODE=fraud, expect DISPUTED, and print the slashing evidence from the marketplace (provider stake before/after).

Both scripts print timestamps proving the agent slept between funding and delivery.

## 7. Testing

- `pytest` green with no network: policy (every predicate, every combination that must dispute), state machine, judge schema validation with mocked model client.
- One integration script (`scripts/smoke_local.sh`) that runs marketplace + agent locally with a fake Gemini stub flag `JUDGE_STUB=1` for CI-style verification without an API key.

## 8. Non-goals (do not build)

- No real registry adapters (demo marketplace registry only).
- No auth/user system beyond the demo token.
- No multi-mission concurrency guarantees beyond Firestore transactions on status transitions.
- No fork/modification of Pacta Protocol.
- No frontend framework.

## 9. Definition of done

- [ ] `make deploy-all` deploys the three services to Cloud Run from a clean checkout.
- [ ] `make demo-happy` ends RELEASED; `make demo-fraud` ends DISPUTED with stake slashed.
- [ ] Trace page renders both missions.
- [ ] Logs in Cloud Console show both wakes as separate request lifecycles (proof of async).
- [ ] All tests pass; README spin-up verified on a machine that is not the dev machine.
- [ ] Architecture diagram committed (mermaid + png).
- [ ] README discloses Pacta as pre-existing MIT dependency.
