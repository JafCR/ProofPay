# proofpay (agent)

The ProofPay agent - the core of the project. A FastAPI service that hires a
provider on a Pacta Protocol marketplace, funds escrow, sleeps, and on delivery
re-verifies every proof before it releases payment or disputes.

See the [top-level README](../README.md) for the full story and the local
spin-up. This file is just the package.

## What's in here

`src/proofpay/`

- `policy.py` - the deterministic release gate (P1..P5). Pure, no I/O. The only
  code path that can authorize a payout.
- `orchestrator.py` - runs the two wake cycles and calls the gate.
- `agent.py` - the Pacta side: the MCP stdio client and the marketplace adapter,
  plus the Phase B ADK `LlmAgent` builder.
- `judge.py` - the structured model calls. `StubJudge` is deterministic and
  keyless (the default); `GeminiJudge` calls Gemini via `google-genai`.
- `models.py` - the pydantic data model (Mission, WakeCycle, ProofCheck, Decision).
- `state.py` - the mission trace store: in-memory locally, Firestore in Phase B.
- `main.py` - the FastAPI app and endpoints.
- `events.py`, `settings.py` - delivery-event parsing and env config.

## Local development

No network, no API key - the stub judge is the default.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

`policy.py` and `state.py` carry the load and have exhaustive offline unit tests
(every predicate, every transition). Run the whole suite from the repo root with
`make test`.
