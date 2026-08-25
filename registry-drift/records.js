'use strict';
// Seed public-registry records for the registry-drift service.
//
// These mirror exactly the records Pacta seeds in its local registry (see
// ../Pacta.Protocol/src/seed.js and docs/CONTRACTS.md §7), so a marketplace
// pointed at this service via REGISTRY_URL behaves identically to the built-in
// `local` adapter - until a record is revoked at runtime (registry drift).
//
// Shape matches Pacta's http adapter contract (../Pacta.Protocol/src/registry.js
// HttpRegistryAdapter): GET {base}/{ref} -> 200 { ref, kind, title, issued_to?,
// details?, created_at? }, 404 when the reference does not exist.

const ISSUED_TO = 'Registro Nacional de Costa Rica';

const RECORDS = {
  'CR-RN-2026-104512': {
    kind: 'incorporation',
    title: 'S.R.L. incorporation certificate',
    details: 'Cédula jurídica 3-102-887766, Registro Nacional de Costa Rica',
  },
  'CR-RN-2026-104513': {
    kind: 'land_eligibility',
    title: 'Land & lodging ownership eligibility registration',
    details: 'Enables land title holding and lodging operation',
  },
  'CR-MUNI-SJ-88231': {
    kind: 'permit',
    title: 'Municipal construction & hotel operation permit',
    details: 'Municipalidad de San José + Ministerio de Salud',
  },
  'CR-HAC-2026-55710': {
    kind: 'tax_filing',
    title: 'Tax registration & compliance filing',
    details: 'Hacienda registration, legal books, UBO declaration',
  },
  // A valid record of the wrong kind, for negative tests (kind mismatch at /complete).
  'CR-RN-2026-200001': {
    kind: 'incorporation',
    title: 'S.A. incorporation certificate (unrelated company)',
    details: 'A valid record of the wrong kind, for negative tests',
  },
};

// Full record as the http contract expects it (ref echoed, issued_to filled in).
function fullRecord(ref) {
  const r = RECORDS[ref];
  if (!r) return null;
  return {
    ref,
    kind: r.kind,
    title: r.title,
    issued_to: ISSUED_TO,
    details: r.details,
    created_at: null,
  };
}

module.exports = { RECORDS, fullRecord, ISSUED_TO };
