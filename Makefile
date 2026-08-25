# ProofPay — everyday commands. See README for the full spin-up story.

.PHONY: test smoke demo-happy demo-fraud seed deploy-all

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

deploy-all:      ## Phase B: deploy marketplace, agent and provider-bot to Cloud Run
	@echo "deploy-all is part of Phase B (Cloud Run) and is not wired yet."
	@exit 1
