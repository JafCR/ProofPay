# CONTRACTS.md · Pacta Protocol (frozen contract for ProofPay)

Source of truth for what `agent.py`, `provider-bot`, `seed.sh` and the demo scripts
consume. Everything below was extracted from the pinned clone at
`../Pacta.Protocol` and **verified empirically** by booting the marketplace on
Node v24.15.0 and driving a full happy-path and a full dispute/slash path through
the real REST API on 2026-08-24.

The clone is **read-only**. `git status` on it is clean; `data/` and `node_modules/`
are gitignored, so booting it does not modify any tracked file.

> If anything here conflicts with `docs/SPEC.md`, the conflict is recorded under
> [§9 Desviaciones detectadas](#9-desviaciones-detectadas). Do not silently resolve
> them in code - flag them.

---

## 1. How it runs

```bash
cd ../Pacta.Protocol
npm install          # 99 packages, ~2s, no build step
PORT=3220 npm run start:pacta
```

- Entry point: `server-pacta.js` → sets `process.env.PACTA=1`, builds the app with
  `pacta: true`, listens on `PORT` (default **3220**).
- Pacta mode turns on: **staking-based vetting**, **registry verification**, the
  agent manifest, and a background **anchor worker** (Merkle-root anchoring). None of
  those need network or keys in the default (`local`) configuration.
- Requires Node **>= 22.5.0** (`package.json engines`). Confirmed booting clean on
  **Node 24.15.0**.
- Health check (unauthenticated): `GET /api/health` → `{"status":"ok","plan":"pacta","ledger_ok":true}`.
- Feature flags at runtime: `GET /api/config` →
  `{"plan":"pacta","registry_adapter":"local","features":{"staking":true,"registry_verification":true,"agent_manifest":true},"hardening":{"api_keys_enforced":false,"rate_limit_per_min":600,"idempotency_keys":true,"provider_webhooks":true}}`

### Boot confirmation (real console output)

```
[PACTA] Agent Services Marketplace running at http://localhost:3220
[PACTA] Registry adapter: local
[PACTA] Anchor worker on (adapter: local).
[PACTA] Seed data loaded (stakes, public registry, unvetted SMB demo).
```

---

## 2. Environment variables

### Marketplace (`server-pacta.js` / `src/app.js` / adapters)

| Var | Default | Effect |
|---|---|---|
| `PORT` | `3220` | Listen port. `EADDRINUSE` exits with a hint. |
| `DB_PATH` | `<repo>/data/pacta.db` | SQLite file. **Delete it to reset to seed** (see §7). |
| `SETTLEMENT_BACKEND` | `ledger` | `ledger` (internal, no chain/keys) or `base-escrow-vault` (loads onchain pkg; fails loudly if missing). Use `ledger` for the demo. |
| `REGISTRY_ADAPTER` | `local` | `local` (seeded SQLite table, deterministic) \| `http` \| `hacienda-cr`. **Keep `local`.** |
| `REGISTRY_URL` | - | If set (and adapter not forced), implies `http` adapter. Leave unset. |
| `REGISTRY_TIMEOUT_MS` | `8000` | Only for `http`/`hacienda-cr`. |
| `REQUIRE_API_KEYS` | unset (**off**) | When `1`, all mutating routes require a Bearer actor key. **Off by default → agent and provider-bot need no auth.** |
| `RATE_LIMIT_PER_MIN` | `600` | Per-IP mutation rate limit. |
| `ANCHOR_AUTOSTART` | on | `0` disables the anchor worker. |
| `ANCHOR_ON_COMPLETE` | on | `0` keeps only the 12h anchor schedule. Safe to ignore for the demo. |

### MCP server (`mcp/server.js`)

| Var | Default | Effect |
|---|---|---|
| `MARKETPLACE_URL` | `http://localhost:3220` | Base URL; the server appends `/api`. Trailing slash stripped. |
| `AGENT_ID` | `1` | The buyer agent id used for `create_engagement` and `get_my_balance`. Seed agent #1 = "Realtor Assistant Agent", $50,000 balance. |

The MCP server talks to the marketplace **over HTTP REST**; it does not share a process.
Both must be running. It connects over **stdio** (`StdioServerTransport`) - this is what
ADK's `MCPToolset` spawns.

---

## 3. MCP tools (what the agent actually calls)

Defined in `mcp/server.js`. **All nine tools the SPEC names exist with the exact SPEC
names** (`agree_to_contract`, `approve_and_release_payment`, etc. - see §9 note 1). There
are 15 tools total; the extras are useful.

**Response envelope** (every tool): MCP content, not raw JSON:
```json
{ "content": [ { "type": "text", "text": "<pretty-printed JSON string>" } ] }
```
On error (any non-2xx from REST): `{ "isError": true, "content": [ { "type": "text", "text": "Error (HTTP 404): <message>" } ] }`.

**Critical:** the `text` payload is a **summary shape with human-formatted strings**, not
the raw REST body. Money is `"$5,000"`, not `500000`. Step keys are renamed
(`requires_registry_proof`, `registry_ref`, `verified_by_platform`). See §9 note 3 - this
is load-bearing for `policy.py` (P5 needs cents).

| Tool | Params | Returns (summary of) |
|---|---|---|
| `search_offers` | `query: str`, `category?: str` | `{results: [offerSummary]}`, ranked by rating then price |
| `get_offer` | `offer_id: int` | one `offerSummary` |
| `create_engagement` | `offer_id: int` | `engagementSummary` (draft). Uses `AGENT_ID` env, **not** a param |
| `agree_to_contract` | `engagement_id: int` | `engagementSummary` (agreed) |
| `fund_escrow` | `engagement_id: int` | `engagementSummary` (funded) |
| `get_engagement` | `engagement_id: int` | `engagementSummary` (current) |
| `wait_for_provider_submission` | `engagement_id: int`, `timeout_seconds?: 1..120` | blocks until state leaves funded/in_progress or times out. **Not for the async design** - see §9 note 5 |
| `verify_registry_reference` | `ref: str` | **raw registry record** (pass-through, not summarized) or MCP error |
| `approve_and_release_payment` | `engagement_id: int` | `engagementSummary` (completed) |
| `reject_and_open_dispute` | `engagement_id: int`, `reason: str` | `engagementSummary` (disputed) |
| `rate_provider` | `engagement_id: int`, `value: "good"\|"bad"` | `engagementSummary` |
| `get_agreement_proof` | `engagement_id: int` | full receipt set (ADR-001 crypto proofs) |
| `verify_agreement_integrity` | `receipt: object` | receipt verification result |
| `get_my_balance` | - | `{agent, balance: "$…"}` |

There is **no MCP tool to resolve a dispute** (arbiter-only) - see §9 note 2.

### `offerSummary` shape (as the agent sees it via MCP)
```json
{
  "offer_id": 1,
  "title": "Establish a Costa Rican company able to buy land and operate a hotel",
  "price": "$5,000",
  "escrow_terms": "20% downpayment, 80% on completion",
  "steps": [
    { "position": 1, "title": "Incorporate S.R.L. company in Costa Rica", "requires_registry_proof": "incorporation" },
    { "position": 2, "title": "Register company for land and hotel ownership eligibility", "requires_registry_proof": "land_eligibility" },
    { "position": 3, "title": "Obtain construction/operation permits for hotel", "requires_registry_proof": "permit" },
    { "position": 4, "title": "Handle all remaining legal filings and compliance", "requires_registry_proof": "tax_filing" }
  ],
  "provider": {
    "smb_id": 1, "name": "Bufete Herrera & Asociados", "location": "Costa Rica",
    "category": "legal", "vetted": true, "collateral_at_stake": "$1,500",
    "rating": "3 good / 1 bad (score 2)"
  }
}
```
Steps with no registry requirement simply omit `requires_registry_proof`.

### `engagementSummary` shape (as the agent sees it via MCP)
```json
{
  "engagement_id": 1, "state": "submitted",
  "title": "Establish a Costa Rican company able to buy land and operate a hotel",
  "price": "$5,000", "escrow_balance": "$1,000",
  "downpayment": "$1,000", "due_on_completion": "$4,000",
  "provider": "Bufete Herrera & Asociados",
  "steps": [
    {
      "position": 1, "title": "Incorporate S.R.L. company in Costa Rica",
      "status": "done", "proof": "Incorporated. Ref CR-RN-2026-104512.",
      "requires_registry_proof": "incorporation",
      "registry_ref": "CR-RN-2026-104512", "verified_by_platform": true
    }
  ]
}
```
Optional keys appear only when set: `dispute_reason`, `resolution`, `your_rating`.
Step keys `proof`, `registry_ref`, `verified_by_platform` appear only after the step is
completed.

---

## 4. REST API (what provider-bot and seed/demo scripts consume)

Base: `http://localhost:3220/api`. JSON in/out. No auth when `REQUIRE_API_KEYS` is off.
All mutations validated server-side; illegal state transitions return **409** with a
descriptive `error`.

### Read
| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/config` | liveness, feature flags |
| GET | `/offers?q=&category=&location=&vetted=` | search (raw shape, `price_cents` etc.) |
| GET | `/offers/:id` | one offer (raw) |
| GET | `/engagements?agent_id=&smb_id=&state=` | **provider-bot polls `?state=funded` and `?state=in_progress`** |
| GET | `/engagements/:id` | full engagement (raw shape) |
| GET | `/smbs/:id` | provider profile incl. `stake_cents`, `exposure_cap_cents` - **slashing evidence** |
| GET | `/registry/:ref` | registry lookup (200 record / 404 not found / 502 unavailable) |
| GET | `/ledger`, `/ledger/invariant` | accounts + transactions; `stake` accounts show live collateral |
| GET | `/disputes` | engagements in `disputed`/`resolved` |
| GET | `/agent/manifest` | machine-readable tool list |

### Provider-side mutations (the SMB / provider-bot)
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/engagements/:id/steps/:stepId/complete` | `{proof_text, proof_url?, registry_ref?}` | **`registry_ref` REQUIRED and verified for registry-anchored steps** (see §5). funded→in_progress on first step. |
| POST | `/engagements/:id/submit` | `{}` | funded/in_progress→submitted. 409 if any step not `done` with proof. |

> `stepId` is a **global** engagement_step id, not the 1-based `position`. In our run
> engagement #1 had step ids 1–4, engagement #2 had 5–8. The provider-bot must read the
> ids from `GET /engagements/:id` (`steps[].id`), which `scripts/smb-bot.js` already does.

### Agent-side mutations (mirrored by the MCP tools)
| Method | Path | Body | State |
|---|---|---|---|
| POST | `/engagements` | `{offer_id, agent_id}` | → draft (reuses an open draft for same agent+offer) |
| POST | `/engagements/:id/agree` | `{}` (custodial sig) | draft→agreed. Returns a signed `receipt`. |
| POST | `/engagements/:id/fund` | `{}` | agreed→funded. Moves upfront% into escrow. |
| POST | `/engagements/:id/approve` | `{}` | submitted→completed. Draws remainder + releases full price. Irreversible. |
| POST | `/engagements/:id/reject` | `{reason}` | submitted→disputed. |
| POST | `/engagements/:id/rate` | `{value:"good"\|"bad"}` | one rating per engagement, only after settlement. |

### Arbiter-only mutation (no MCP tool - needed for the fraud demo's slash)
| Method | Path | Body | State |
|---|---|---|---|
| POST | `/engagements/:id/resolve` | `{ruling:"release"\|"refund"\|"split"}` | disputed→resolved. **This is where the stake is slashed.** |

---

## 5. Engagement lifecycle & registry enforcement

**States** (validated in `src/api.js` `TRANSITIONS`):
```
draft → agreed → funded → in_progress → submitted → completed
                                              ↘ disputed → resolved
```
Mapping to SPEC's mission status: the agent's Wake-1 walks `draft→agreed→funded`; the
provider drives `funded→in_progress→submitted`; the agent's Wake-2 ends at `completed`
(approve) or `disputed` (reject); an arbiter then `resolved`.

**Registry-anchored proof enforcement - happens at `/complete`, server-side** (`src/api.js`):
for a step whose `verification_kind` is set, the provider MUST pass `registry_ref`, and
the marketplace does the lookup itself:
- missing `registry_ref` → **400** `this step requires a public registry reference (kind: incorporation)`
- ref not in registry → **409** `registry reference 'CR-RN-2026-999999' not found in the public registry`
- ref exists but wrong kind → **409** `registry record 'CR-MUNI-SJ-88231' is a 'permit' record; this step requires 'incorporation'`
- ref exists and kind matches → stored with `proof_verified: true`

**Consequence (see §9 note 4):** a fraudulent or wrong-kind reference **cannot reach
`submitted` state**. Every proof the agent sees in Wake-2 is already `verified_by_platform: true`.

### Raw engagement step shape (from `GET /engagements/:id`)
```json
{
  "id": 1, "position": 1,
  "title": "Incorporate S.R.L. company in Costa Rica",
  "description": "Draft and register articles of incorporation with the National Registry.",
  "status": "done",
  "proof_text": "Incorporated. Ref CR-RN-2026-104512.",
  "proof_url": null,
  "completed_at": "2026-08-25 05:37:35",
  "verification_kind": "incorporation",
  "proof_registry_ref": "CR-RN-2026-104512",
  "proof_verified": true
}
```
`status` ∈ `pending` | `done`. Top-level engagement also carries `price_cents`,
`upfront_pct`, `upfront_cents`, `remaining_cents`, `escrow_balance_cents`,
`steps_done`, `steps_total`, `agreement_hash`, `signatures{buyer,provider}`,
`dispute_reason`, `resolution`, `rating`.

---

## 6. Registry records & verify responses

`verify_registry_reference` / `GET /api/registry/:ref` returns the record verbatim.

**Valid, correct-kind (real response):**
```json
{
  "ref": "CR-RN-2026-104512", "kind": "incorporation",
  "title": "S.R.L. incorporation certificate",
  "issued_to": "Registro Nacional de Costa Rica",
  "details": "Cédula jurídica 3-102-887766, Registro Nacional de Costa Rica",
  "created_at": "2026-08-25 05:36:55", "source": "local"
}
```
Record shape: `{ref, kind, title, issued_to, details, created_at, source}`.

**Reference does NOT exist** (the fraud pattern `CR-RN-2026-999999`):
- REST: HTTP **404** `{"error":"no public record with reference 'CR-RN-2026-999999'"}`
- MCP: `{"isError":true,"content":[{"type":"text","text":"Error (HTTP 404): no public record with reference 'CR-RN-2026-999999'"}]}`

**Valid but wrong kind** (`CR-RN-2026-200001` is a real `incorporation` record seeded for
negative tests): the registry lookup **succeeds** (200 / record). Kind mismatch is caught by
comparing `record.kind` to the step's required kind - the marketplace does this at
`/complete`; the agent's `policy.py` re-does it as P3.

**Registry unavailable** (network/upstream fault, only possible with `http`/`hacienda-cr`
adapters): HTTP **502** `RegistryUnavailableError`. The protocol refuses to guess. `policy.py`
must treat 502 as "cannot decide" (do NOT release, do NOT auto-slash) - distinct from a 404
"does not exist". With the `local` adapter this never occurs.

---

## 7. Seed data & reset

Seeded once, only when the DB is empty (`src/seed.js` `seedIfEmpty`, guarded by
`SELECT COUNT(*) FROM agents`).

- **Buyer agent:** id **1**, "Realtor Assistant Agent", balance **$50,000**. (This is the
  `AGENT_ID` the MCP server uses.)
- **Arbiter:** id 1, "Marketplace Arbiter".
- **SMBs / offers** (all vetted via seeded stake except the last):

| smb_id | Name | Category | offer_id | Price | Upfront | Stake (cap) | Rating | Registry-anchored steps? |
|---|---|---|---|---|---|---|---|---|
| 1 | **Bufete Herrera & Asociados** | legal | 1 | $5,000 | 20% | $1,500 ($7,500) | 3g/1b | **Yes - the flagship 4-step demo** |
| 2 | LexCorp Legal Solutions | legal | 2 | $4,500 | 30% | $1,000 ($5,000) | 2g/0b | No |
| 3 | Tico Adventures Tours | tourism | 3 | $1,200 | 50% | $500 | 4g/0b | No |
| 4 | Pura Vida Realty | real-estate | 4 | $2,000 | 25% | $500 | 1g/1b | No |
| 5 | Sandoval Accounting Group | accounting | 5 | $1,500 | 20% | $500 | 2g/1b | No |
| 6 | Horizonte Legal Panamá | legal | 6 | $3,800 | 20% | $800 | 1g/0b | No |
| 7 | Island Estates Development | real-estate | 7 | $300,000 | 20% | $5,000 ($25,000) | 3g/0b | No (upfront $60k > agent balance → funds gate) |
| 8 | **Despacho Sin Garantía** | legal | 8 | $900 | 50% | **none → unvetted** | 0g/0b | No (vetting gate: `create_engagement` returns 409) |

**Offer #1 (Bufete Herrera) is the one ProofPay hires.** Its four steps require, in order,
the registry kinds: `incorporation`, `land_eligibility`, `permit`, `tax_filing`.

### Seeded public registry records
| ref | kind | note |
|---|---|---|
| `CR-RN-2026-104512` | `incorporation` | valid - step 1 |
| `CR-RN-2026-104513` | `land_eligibility` | valid - step 2 |
| `CR-MUNI-SJ-88231` | `permit` | valid - step 3 |
| `CR-HAC-2026-55710` | `tax_filing` | valid - step 4 |
| `CR-RN-2026-200001` | `incorporation` | **valid record of the wrong kind**, for negative tests |

- **Valid example ref:** `CR-RN-2026-104512`.
- **Plausible-but-invalid (fraud) pattern:** `CR-RN-2026-999999` - same `CR-RN-2026-######`
  format, not in the registry → 404. (The SPEC/`smb-bot` fraud value.)

### Reset to seed
No reset endpoint. Delete the SQLite files and restart:
```bash
rm -f ../Pacta.Protocol/data/pacta.db ../Pacta.Protocol/data/pacta.db-shm ../Pacta.Protocol/data/pacta.db-wal
PORT=3220 npm run start:pacta   # re-seeds on empty DB
```
`data/` is gitignored, so this never touches tracked files. `seed.sh` should do exactly this.

---

## 8. Slashing evidence (for `demo-fraud`)

Verified end-to-end. Slashing happens **only on `POST /resolve`** (arbiter), not on the
agent's `reject`. Sequence that produced real slashing:

1. Agent `reject` (dispute) → state `disputed`, `dispute_reason` stored.
2. Arbiter `POST /engagements/:id/resolve {"ruling":"refund"}` → state `resolved`.
3. Stake read from `GET /smbs/1`: **`stake_cents` 150000 → 50000**.

Slash amount = **percentage of engagement PRICE** (not of stake), capped to the stake
balance (`src/staking.js`):
- `refund` → **20%** of price · `split` → **10%** · `release` → **0%**
- Here: 20% × $5,000 = **$1,000** slashed from a $1,500 stake → $500 left.

`demo-fraud` should snapshot `GET /smbs/1` `stake_cents` before and after, and run the
arbiter resolve to make the slash visible.

---

## 9. Desviaciones detectadas

> These are places where `docs/SPEC.md` does not match Pacta reality. **Not resolved here** -
> raised for Jaf / the policy owner. Several bear on `policy.py`, which needs explicit approval.

**Note 1 - Tool names all match (good news).** The SPEC guessed the MCP tool names and they
are all correct: `search_offers`, `create_engagement`, `agree_to_contract`, `fund_escrow`,
`get_engagement`, `verify_registry_reference`, `approve_and_release_payment`,
`reject_and_open_dispute`, `rate_provider`. No renaming needed.

**Note 2 - Dispute ≠ slash; slashing needs an arbiter, and there is no MCP tool for it.**
`reject_and_open_dispute` only moves the engagement to `disputed`. The stake is slashed only
when an **arbiter** calls `POST /engagements/:id/resolve`, which the MCP server does **not**
expose. SPEC §6 `demo-fraud` ("expect DISPUTED with stake slashed") therefore needs an extra
actor: the demo script (or provider-bot/a helper) must POST `/resolve` as the arbiter to
produce the slash. The agent alone cannot cause a slash. **Decision needed:** who plays the
arbiter in the demo, and does the "slashing evidence" come from an arbiter resolve or just
from showing the stake-at-risk?

**Note 3 - MCP returns human strings, not cents; `policy.py` P5 needs numbers.** MCP
`get_engagement` gives `price: "$5,000"`, `escrow_balance: "$1,000"` - formatted strings.
Policy predicate **P5 (escrow covers release)** and any numeric reasoning cannot be done on
these directly. Options for the agent: (a) parse the `$` strings, or (b) have `state.py`/agent
also read the raw REST `GET /engagements/:id` (`escrow_balance_cents`, `price_cents`,
`remaining_cents`) alongside MCP. P3 (kind match) IS doable via MCP: step
`requires_registry_proof` vs the raw `kind` from `verify_registry_reference`. **Decision
needed:** does the agent read REST for cents, or parse MCP strings? (The SPEC says "consume the
unmodified MCP server" - reading REST for numbers is a reasonable supplement, but flag it.)

**Note 4 - The fraud can't reach the agent the way SPEC describes.** SPEC §2.3/§6 has the
provider submit a nonexistent ref (`CR-RN-2026-999999`) and the agent catch it during
re-verification. **Pacta blocks this server-side at `/complete`** (409, verified above): a bad
or wrong-kind `registry_ref` is rejected on submission, so the engagement never reaches
`submitted`, the delivery event never fires, and there is nothing for the agent to
re-verify-and-dispute. With the `local` adapter, every proof the agent sees is already
`proof_verified: true`, so `policy.py` P2/P3 (re-verify + kind match) will essentially always
pass - the independent check is real but redundant with the platform's. **This is the biggest
gap.** Candidate reframings (for Jaf to choose, do not implement):
  - (a) Fraud demo = provider-bot MODE=fraud attempts the bad ref, gets 409, engagement stalls
    in `funded`; the agent's **sweep** notices non-delivery and disputes/abandons (a
    "non-delivery" dispute, not a "bad-proof" dispute). Slashing then needs the arbiter (Note 2).
  - (b) Hire LexCorp (offer #2, no registry-anchored steps): the provider submits free-text
    `proof_text` with no registry anchor; the LLM `assess_proof` vetoes on garbage proof → P4
    fails → dispute. This is an **LLM-driven** dispute, not registry-driven, and contradicts the
    SPEC's registry-centric fraud story.
  - (c) Point the agent's independent `verify_registry_reference` at a **different** registry
    adapter than the marketplace used, so a ref valid to the platform is unknown to the agent's
    check → genuine P2 failure. Requires config the SPEC doesn't describe.
  The SPEC's stated mechanism ("agent independently catches a nonexistent reference the provider
  slipped through") is not reproducible against unmodified Pacta. **Policy/design decision required.**

**Note 5 - `wait_for_provider_submission` contradicts the async "agent sleeps" design.** The
MCP server offers a blocking `wait_for_provider_submission` (polls up to 120s). ProofPay's whole
thesis is that the agent **exits** after funding and is re-woken by Pub/Sub. Do **not** use this
tool in Wake-1; it would keep the process alive and defeat the "it slept for days" demo. Noted so
nobody wires it in by reflex.

**Note 6 - `create_engagement` ignores an `agent_id` argument.** The MCP tool takes only
`offer_id` and injects `AGENT_ID` from env. The agent cannot choose the buyer per-call; it is
fixed at MCP-spawn time. Fine for a single-agent demo; just don't expect a param.

**Note 7 - Vetting gate is real and firm.** `create_engagement` against the unvetted SMB
(Despacho Sin Garantía, offer #8) returns **409** `'Despacho Sin Garantía' is not vetted…`. The
SPEC's "zero-collateral providers are a different risk class" instruction is enforced by Pacta
itself at engagement creation, not just by the LLM's judgment. Good - but it means the LLM will
never even get to fund an unvetted provider; the collateral guidance mostly affects ranking among
*vetted* providers.
