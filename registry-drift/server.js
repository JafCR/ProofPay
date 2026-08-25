'use strict';
// registry-drift - a tiny public-registry gateway for the cloud fraud demo.
//
// Why it exists: in the LOCAL fraud demo, demo_fraud.sh simulates "registry drift"
// (a regulator annulling a credential after it was filed) by deleting a row from
// Pacta's SQLite data at runtime. That trick needs shell access to the marketplace's
// disk, which Cloud Run does not give you. So in the cloud we point the marketplace's
// pluggable registry at THIS service (REGISTRY_URL) instead of its built-in `local`
// adapter, and revoke a record over HTTP.
//
// It speaks Pacta's http registry contract 1:1 (../Pacta.Protocol/src/registry.js):
//   GET  /:ref            -> 200 { ref, kind, title, issued_to, details, created_at }
//                            404 { error } when the ref is unknown OR has been revoked
// Plus a control surface for the demo (token-guarded):
//   POST /revoke/:ref     -> mark a seeded record as revoked (drift)
//   POST /restore/:ref    -> undo a revocation (so demos are repeatable)
//   POST /reset           -> clear all revocations
//   GET  /health          -> liveness + which refs are currently revoked
//
// No dependencies (Node built-in http, Node >= 22.5). No secrets in the file: the
// revoke control is guarded by REVOKE_TOKEN from the environment.

const http = require('node:http');
const { fullRecord } = require('./records');

const PORT = Number(process.env.PORT || 4555);
const REVOKE_TOKEN = process.env.REVOKE_TOKEN || '';

// Refs revoked at runtime. In-process state (like Pacta's own demo marketplace):
// a restart returns to the seed set, which is exactly what a fresh demo wants.
const revoked = new Set();

const send = (res, status, body) => {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(text);
};

const iso = () => new Date().toISOString();
const log = (msg) => console.log(`${iso()} [registry-drift] ${msg}`);

// The revoke/restore/reset controls require the shared token when one is configured.
// Accepts `Authorization: Bearer <token>` or `X-Revoke-Token: <token>`.
function authorized(req) {
  if (!REVOKE_TOKEN) return true; // unset = open (local dev only); logged at startup
  const bearer = /^Bearer\s+(.+)$/i.exec(req.headers['authorization'] || '');
  const presented = (bearer && bearer[1]) || req.headers['x-revoke-token'] || '';
  return presented === REVOKE_TOKEN;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const parts = url.pathname.split('/').filter(Boolean);
  const method = req.method || 'GET';

  // GET /health
  if (method === 'GET' && parts[0] === 'health') {
    return send(res, 200, {
      status: 'ok',
      adapter: 'registry-drift',
      revoked: [...revoked],
    });
  }

  // Control surface (token-guarded): /revoke/:ref, /restore/:ref, /reset
  if (method === 'POST' && (parts[0] === 'revoke' || parts[0] === 'restore')) {
    if (!authorized(req)) return send(res, 401, { error: 'invalid or missing revoke token' });
    const ref = decodeURIComponent(parts[1] || '');
    if (!ref) return send(res, 400, { error: 'missing ref' });
    if (!fullRecord(ref)) return send(res, 404, { error: `unknown seed ref '${ref}'` });
    if (parts[0] === 'revoke') {
      revoked.add(ref);
      log(`REVOKED ${ref} - registry now returns 404 for it (drift)`);
      return send(res, 200, { ref, revoked: true });
    }
    revoked.delete(ref);
    log(`restored ${ref}`);
    return send(res, 200, { ref, revoked: false });
  }
  if (method === 'POST' && parts[0] === 'reset') {
    if (!authorized(req)) return send(res, 401, { error: 'invalid or missing revoke token' });
    revoked.clear();
    log('reset - all revocations cleared');
    return send(res, 200, { revoked: [] });
  }

  // The Pacta http registry contract: GET /:ref
  if (method === 'GET' && parts.length === 1) {
    const ref = decodeURIComponent(parts[0]);
    if (revoked.has(ref)) return send(res, 404, { error: `reference '${ref}' has been revoked` });
    const record = fullRecord(ref);
    if (!record) return send(res, 404, { error: `no public record with reference '${ref}'` });
    return send(res, 200, record);
  }

  return send(res, 404, { error: `no such route: ${method} ${url.pathname}` });
});

server.listen(PORT, () => {
  log(`listening on http://localhost:${PORT} (Pacta http registry contract)`);
  if (!REVOKE_TOKEN) log('WARNING: REVOKE_TOKEN is unset - the revoke control is OPEN. Set it in any shared environment.');
});
