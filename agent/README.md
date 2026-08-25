# proofpay (agent)

The ProofPay agent package. This is the project's core deployable (SPEC §2.2).

Local development (no network, no API key):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Modules land in build order (see `../docs/SPEC.md`): `policy.py` and `state.py`
carry the load and are covered by pure, offline unit tests. `judge.py`,
`agent.py`, and `main.py` arrive in later tasks.
