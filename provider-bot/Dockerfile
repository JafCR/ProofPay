# ProofPay provider-bot - simulated SMB back office (SPEC §2.3).
# Node 22 (built-in fetch). Installs @google-cloud/pubsub so cloud runs can publish
# the delivery event to Pub/Sub; local runs (no PUBSUB_TOPIC) never load it.
FROM node:22-bookworm-slim

WORKDIR /app
COPY package.json ./
# No lockfile committed for the bot; `npm install` resolves @google-cloud/pubsub.
RUN npm install --omit=dev
COPY src ./src

# On Cloud Run the bot is a worker, not a server. It is deployed as a Cloud Run
# Job (see deploy.sh), so it runs to completion instead of serving a port.
CMD ["node", "src/bot.js"]
