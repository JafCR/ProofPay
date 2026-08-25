# ProofPay - everyday commands. See README for the full spin-up story.

# Cloud (Phase B) - override on the command line if needed.
GCP_PROJECT ?= optimal-signer-506615-d5
GCP_REGION  ?= us-central1

.PHONY: test smoke demo-happy demo-fraud seed stop \
        deploy-all deploy-registry-drift deploy-marketplace deploy-agent deploy-provider wire-cloud

test:            ## run the whole python suite (no network, no API key)
	cd agent && .venv/bin/python -m pytest -q

smoke:           ## full local loop twice: honest -> RELEASED, fraud -> DISPUTED
	scripts/smoke_local.sh

demo-happy:      ## honest provider, 90s work delay, ends RELEASED
	scripts/demo_happy.sh

demo-fraud:      ## forged ref blocked + registry drift, ends DISPUTED + slash
	scripts/demo_fraud.sh

seed:            ## reset the local marketplace to its seed state
	scripts/seed.sh

stop:            ## stop everything the demos left running (agent, marketplace, bot)
	-@lsof -ti tcp:8080 | xargs kill 2>/dev/null || true
	-@lsof -ti tcp:3220 | xargs kill 2>/dev/null || true
	-@pkill -f "provider-bot/src/bot.js" 2>/dev/null || true
	@echo "stopped."

wire-cloud:      ## Phase B: enable APIs, Artifact Registry, Pub/Sub topic+push, Scheduler, SAs (idempotent)
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) scripts/wire_cloud.sh

deploy-all:      ## Phase B: build+deploy all 4 services in order, chaining URLs
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) scripts/deploy_all.sh

deploy-registry-drift: ## Phase B: build+deploy the registry-drift service
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) registry-drift/deploy.sh

deploy-marketplace:    ## Phase B: build+deploy the Pacta marketplace (pass REGISTRY_URL=... for the fraud demo)
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) marketplace/deploy.sh

deploy-agent:          ## Phase B: build+deploy the ProofPay agent (resolves MARKETPLACE_URL)
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) agent/deploy.sh

deploy-provider:       ## Phase B: build+deploy the provider-bot Cloud Run Job
	PROJECT=$(GCP_PROJECT) REGION=$(GCP_REGION) provider-bot/deploy.sh
