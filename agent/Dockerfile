# ProofPay agent — the project (SPEC §2.2). Python app + a Node MCP sidecar.
#
# Build context is the ProofPay repo ROOT (it copies agent/ and web/); build it with
# `-f agent/Dockerfile .` from the repo root (agent/deploy.sh does this via Cloud Build).
#
# Base is node:22 so the MCP sidecar has Node; Debian bookworm also ships Python 3.11,
# which we use for the FastAPI app. The agent talks to Pacta over the UNMODIFIED MCP
# server (mcp/server.js), vendored here by cloning Pacta at the pinned commit — the
# same transport ADK's MCPToolset would spawn. Runtime money-moving still goes only
# through that server (SPEC §2.2, §3).
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# --- Vendor Pacta's MCP server (unmodified) at the pinned commit -------------- #
ARG PACTA_COMMIT=2ce2abe1d2dadeafcf16eaad847a39cfcf8b91a7
WORKDIR /opt/pacta
RUN git clone https://github.com/Pacta-Protocol/Pacta.Protocol.git . \
 && git checkout "${PACTA_COMMIT}" \
 && npm ci --omit=dev
# The agent spawns `node $PACTA_MCP_SERVER` for each MCP call (agent.py).
ENV PACTA_MCP_SERVER=/opt/pacta/mcp/server.js

# --- Install the agent (SPEC R1/R2 Google deps via the [google] extra) -------- #
WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY agent/pyproject.toml agent/README.md /app/
COPY agent/src /app/src
# google-cloud-firestore is the Firestore state backend (STATE_BACKEND=firestore).
# It belongs in the pyproject [google] extra alongside google-genai; installed
# explicitly here until that extra is extended (coordinate with the settings owner).
RUN pip install --no-cache-dir ".[google]" google-cloud-firestore

# --- Styled trace page (web/, SPEC §2.4), served by the app at / and /fixtures - #
COPY web /app/web
ENV PROOFPAY_WEB_DIR=/app/web

# Runtime defaults; the rest (GOOGLE_CLOUD_PROJECT, MARKETPLACE_URL, DEMO_TOKEN,
# DELIVERY_DEADLINE_SECONDS, STATE_BACKEND) are supplied at deploy time.
ENV PORT=8080 \
    GOOGLE_CLOUD_LOCATION=global \
    GEMINI_MODEL=gemini-3.5-flash \
    JUDGE_STUB=0

EXPOSE 8080
# uvicorn factory mode builds the app at startup (real marketplace + judge).
CMD ["sh", "-c", "exec uvicorn proofpay.main:create_app --factory --host 0.0.0.0 --port ${PORT}"]
