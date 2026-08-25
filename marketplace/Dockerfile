# Pacta marketplace (pre-existing MIT dependency) — deployed UNMODIFIED.
#
# The image clones Pacta.Protocol from GitHub at a PINNED commit and runs it as-is;
# ProofPay never vendors a changed copy (SPEC §2.1). The build
# arg PACTA_COMMIT pins the exact revision of the local clone this repo was built
# against (git -C ../Pacta.Protocol rev-parse HEAD).
#
# Node 24: Pacta persists to SQLite via the built-in `node:sqlite` (DatabaseSync),
# which runs without an experimental flag only on Node >= 23 — this mirrors Pacta's
# own Dockerfile (node:24-alpine). (Node 22 would need --experimental-sqlite.)
FROM node:24-alpine

RUN apk add --no-cache git
WORKDIR /app

ARG PACTA_COMMIT=2ce2abe1d2dadeafcf16eaad847a39cfcf8b91a7
RUN git clone https://github.com/Pacta-Protocol/Pacta.Protocol.git . \
 && git checkout "${PACTA_COMMIT}" \
 && npm ci --omit=dev

# server-pacta.js sets PACTA=1 itself and listens on $PORT (default 3220). Cloud Run
# injects $PORT (8080). Registry/settlement are parametrized purely by env at deploy:
#   REGISTRY_URL     -> point at the registry-drift service to enable the cloud fraud
#                       demo (unset = Pacta's built-in `local` seeded registry).
#   REGISTRY_ADAPTER -> defaults to `http` when REGISTRY_URL is set (Pacta's own logic).
ENV PORT=8080
EXPOSE 8080

CMD ["node", "server-pacta.js"]
