'use strict';
// ProofPay provider-bot — simulated SMB back office (SPEC §2.3, reframed by DECISIONS.md 2026-08-25).
//
// Lifecycle it drives (Pacta REST, see docs/CONTRACTS.md §4/§5):
//   the agent's Wake-1 leaves the engagement in `funded`; this bot polls for its own
//   provider's funded/in_progress engagements, sleeps DELAY_SECONDS (proving the agent
//   itself slept), then completes each step and submits. Wake-2 of the agent then verifies.
//
// Two demo modes:
//   MODE=honest  completes every step with the correct seeded registry reference, submits,
//                and notifies delivery. The agent releases payment. (happy path)
//   MODE=fraud   completes the first registry-anchored step honestly, then tries a forged
//                reference (CR-RN-2026-999999) on the SECOND registry-anchored step. Pacta
//                rejects it server-side at /complete with HTTP 409 (verified — CONTRACTS.md
//                §5/§9 n.4). That block is layer-1 evidence for the demo, logged loudly. The
//                bot then FALLS BACK to the correct valid reference for that step, finishes all
//                four steps, and SUBMITS. It does NOT post the delivery webhook in fraud mode:
//                the demo script owns the event timing — it revokes one registry record
//                (registry drift) and then fires the delivery event itself. So when the agent
//                re-verifies every reference at release time, it gets a 404 on the revoked one
//                and opens a dispute (policy P2 fails). (fraud path)
//
// Config is 100% environment, no secrets:
//   MODE                   honest | fraud                     (default: honest)
//   DELAY_SECONDS          "work" delay before completing     (default: 90)
//   MARKETPLACE_URL        Pacta base URL                      (default: http://localhost:3220)
//   SMB_ID                 provider whose engagements we serve (default: 1 = Bufete Herrera)
//   POLL_INTERVAL_SECONDS  marketplace poll cadence            (default: 3)
//   DELIVERY_WEBHOOK_URL   where to POST the delivery event    (default: http://localhost:8080/events/delivery)
//   PUBSUB_TOPIC           if set, publish delivery to Pub/Sub  (default: unset → HTTP webhook)
//   MISSION_ID             optional passthrough (see below)    (default: unset)

const MODE = (process.env.MODE || 'honest').toLowerCase();
const DELAY_SECONDS = Number(process.env.DELAY_SECONDS ?? 90);
const BASE = (process.env.MARKETPLACE_URL || 'http://localhost:3220').replace(/\/$/, '');
const SMB_ID = Number(process.env.SMB_ID ?? 1);
const POLL_INTERVAL_SECONDS = Number(process.env.POLL_INTERVAL_SECONDS ?? 3);
const DELIVERY_WEBHOOK_URL = process.env.DELIVERY_WEBHOOK_URL || 'http://localhost:8080/events/delivery';
const PUBSUB_TOPIC = process.env.PUBSUB_TOPIC; // set in cloud → publish to Pub/Sub instead of HTTP
const MISSION_ID = process.env.MISSION_ID; // usually unset — see notifyDelivery()

// The SMB's "filing receipts" — references it earned by actually doing the work at each
// authority, keyed by the step's verification kind. All four are seeded in the mock public
// registry (CONTRACTS.md §7). Mapping by kind (not by position) matches Pacta's own smb-bot.
const RECEIPTS = {
  incorporation: 'CR-RN-2026-104512',
  land_eligibility: 'CR-RN-2026-104513',
  permit: 'CR-MUNI-SJ-88231',
  tax_filing: 'CR-HAC-2026-55710',
};

// The plausible-but-nonexistent reference for MODE=fraud. Same CR-RN-2026-###### shape as a
// real incorporation ref, but absent from the registry → Pacta returns 409 at /complete.
const FRAUD_REF = 'CR-RN-2026-999999';

const iso = () => new Date().toISOString();
const log = (msg) => console.log(`${iso()} [provider-bot] ${msg}`);
const banner = (msg) => {
  const bar = '='.repeat(72);
  console.log(`\n${bar}\n${iso()} [provider-bot] ${msg}\n${bar}\n`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Thin REST client. On non-2xx throws an Error carrying the HTTP status so callers can
// distinguish the expected 409 (forged reference) from real failures.
async function api(method, path, body) {
  const res = await fetch(`${BASE}/api${path}`, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error((data && data.error) || `${method} ${path} → HTTP ${res.status}`);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

// Notify the agent that delivery happened. Payload JSON: { "engagement_id": <int> }
// (+ "mission_id" only if MISSION_ID is set). mission_id is intentionally usually
// omitted: Pacta has no field to carry a mission_id on the engagement (CONTRACTS.md),
// so the agent resolves the mission from engagement_id on its side.
//
// Two transports, ONE parser on the agent side (events.py, SPEC §2.2):
//   - PUBSUB_TOPIC set (cloud): publish the raw payload to Pub/Sub. The push
//     subscription wraps it as { "message": { "data": base64(payload) } } on delivery.
//   - otherwise (local): POST that same envelope over HTTP ourselves.
// So the agent parser is identical in both worlds.
async function publishToPubSub(payload) {
  // Lazy dynamic import (this file is an ES module): only loaded in cloud, so local
  // runs never need the library installed.
  const { PubSub } = await import('@google-cloud/pubsub');
  const messageId = await new PubSub()
    .topic(PUBSUB_TOPIC)
    .publishMessage({ data: Buffer.from(JSON.stringify(payload)) });
  return messageId;
}

async function notifyDelivery(engagementId) {
  const payload = { engagement_id: engagementId };
  if (MISSION_ID) payload.mission_id = MISSION_ID;

  if (PUBSUB_TOPIC) {
    try {
      const messageId = await publishToPubSub(payload);
      log(`delivery published to Pub/Sub topic "${PUBSUB_TOPIC}" (messageId ${messageId}) for engagement #${engagementId}`);
      return;
    } catch (err) {
      // Fall back to HTTP so a Pub/Sub misconfig never strands the demo.
      log(`Pub/Sub publish failed (${err.message}); falling back to HTTP webhook`);
    }
  }

  const envelope = { message: { data: Buffer.from(JSON.stringify(payload)).toString('base64') } };
  try {
    const res = await fetch(DELIVERY_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(envelope),
    });
    log(`delivery event POSTed to ${DELIVERY_WEBHOOK_URL} for engagement #${engagementId} → HTTP ${res.status}`);
  } catch (err) {
    // Non-fatal: the agent's periodic sweep is the fallback if the event is lost (SPEC §2.2).
    log(`delivery event POST failed (${err.message}); agent sweep will pick it up`);
  }
}

// Complete one step with the given reference (or none, for non-registry steps).
async function completeStep(e, step, registryRef) {
  const requiresRegistry = Boolean(step.verification_kind);
  const body = {
    proof_text: `Completed: ${step.title}.` +
      (requiresRegistry ? ` Official filing reference: ${registryRef}.` : ' Deliverable sent to client.'),
  };
  if (requiresRegistry) body.registry_ref = registryRef;
  return api('POST', `/engagements/${e.id}/steps/${step.id}/complete`, body);
}

// Do the work for one engagement: sleep the "work" delay, complete each pending step
// (attaching the registry reference where required), then submit.
async function workOn(e) {
  const kind = MODE === 'fraud' ? 'FRAUD' : 'HONEST';
  log(`engagement #${e.id} funded — ${e.smb.name} accepts "${e.title}" [MODE=${kind}]`);
  log(`sleeping DELAY_SECONDS=${DELAY_SECONDS}s to simulate real-world work (the agent is asleep during this window)...`);
  await sleep(DELAY_SECONDS * 1000);
  log(`engagement #${e.id} work window elapsed — filing step proofs now`);

  let registryStepsSeen = 0;
  for (const step of e.steps) {
    if (step.status === 'done') continue;

    const requiresRegistry = Boolean(step.verification_kind);
    if (requiresRegistry) registryStepsSeen += 1;
    const validRef = requiresRegistry ? RECEIPTS[step.verification_kind] : undefined;

    // In fraud mode, try a forged reference on the 2nd registry-anchored step. Pacta blocks it
    // server-side (409) — layer-1 evidence for the demo — then fall back to the real reference
    // and carry on. The engagement still reaches `submitted`; the fraud surfaces later, when the
    // demo script revokes a registry record and the agent re-verifies at release time.
    if (MODE === 'fraud' && requiresRegistry && registryStepsSeen === 2) {
      try {
        await completeStep(e, step, FRAUD_REF);
        // A forged reference must be rejected; reaching here means the protocol let it through.
        log(`  WARNING: forged ref ${FRAUD_REF} was NOT rejected — marketplace accepted it unexpectedly.`);
      } catch (err) {
        if (err.status !== 409) throw err;
        banner('PROTOCOL BLOCKED FORGED REFERENCE');
        log(`  step ${step.position}/${e.steps.length} forged attempt REJECTED at /complete`);
        log(`  forged registry_ref: ${FRAUD_REF}`);
        log(`  marketplace response: HTTP 409 — ${err.message}`);
        log(`  forged ref blocked by protocol; falling back to valid ref — submission will be revoked by registry drift`);
        await completeStep(e, step, validRef);
        log(`  step ${step.position}/${e.steps.length} done on fallback: ${step.title} (registry ${validRef})`);
      }
      continue;
    }

    // Normal completion: honest mode, or any non-forged step in fraud mode.
    await completeStep(e, step, validRef);
    log(`  step ${step.position}/${e.steps.length} done: ${step.title}` + (validRef ? ` (registry ${validRef})` : ''));
  }

  await api('POST', `/engagements/${e.id}/submit`, {});
  log(`engagement #${e.id} submitted for the agent's verification`);

  if (MODE === 'fraud') {
    // The demo script owns the delivery event in fraud mode: it revokes a registry record first,
    // then fires the webhook itself, so the agent's re-verification catches the drift (P2).
    log(`fraud mode: NOT posting the delivery webhook — the demo script fires the event after revoking a registry record.`);
  } else {
    await notifyDelivery(e.id);
  }
}

async function pollOnce(processed) {
  // Only this provider's engagements, only the states we can act on.
  const funded = await api('GET', `/engagements?smb_id=${SMB_ID}&state=funded`);
  const inProgress = await api('GET', `/engagements?smb_id=${SMB_ID}&state=in_progress`);
  for (const e of [...funded, ...inProgress]) {
    if (processed.has(e.id)) continue;
    processed.add(e.id); // claim it before the await so overlapping polls don't double-fire
    workOn(e).catch((err) => {
      log(`error on engagement #${e.id}: ${err.message}`);
      processed.delete(e.id); // let a later poll retry genuine (non-fraud) failures
    });
  }
}

async function main() {
  log(`starting — MODE=${MODE}, SMB_ID=${SMB_ID}, watching ${BASE} for funded engagements`);
  const transport = PUBSUB_TOPIC ? `Pub/Sub topic "${PUBSUB_TOPIC}"` : `HTTP ${DELIVERY_WEBHOOK_URL}`;
  log(`config: DELAY_SECONDS=${DELAY_SECONDS}, POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS}, delivery via ${transport}`);
  if (MODE !== 'honest' && MODE !== 'fraud') {
    log(`WARNING: unknown MODE="${MODE}"; expected "honest" or "fraud". Treating as honest.`);
  }
  const processed = new Set();
  for (;;) {
    try {
      await pollOnce(processed);
    } catch (err) {
      // Marketplace not up yet, or a transient poll error — keep watching.
      log(`poll error: ${err.message}`);
    }
    await sleep(POLL_INTERVAL_SECONDS * 1000);
  }
}

// When PORT is set (Cloud Run *service* mode, min-instances=1) expose a trivial
// health endpoint so the platform keeps the polling loop alive. As a Job or a
// local process PORT is unset and no server starts.
if (process.env.PORT) {
  const { createServer } = await import('node:http');
  createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', mode: MODE, smb_id: SMB_ID }));
  }).listen(Number(process.env.PORT), () => log(`health endpoint on :${process.env.PORT} (service mode)`));
}

main().catch((err) => {
  log(`fatal: ${err.stack || err.message}`);
  process.exit(1);
});
