"""Environment configuration for ProofPay (SPEC §5).

All configuration is read from environment variables here and nowhere else; the
rest of the codebase imports :class:`Settings` and never touches ``os.environ``
or hard-codes a URL, model name, or token. Defaults are chosen so the package
runs locally with no cloud project and no API key (``JUDGE_STUB`` on).
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, ConfigDict

# Requirement from SPEC §5 / R1: the pinned model string is verified against the
# live Gemini model list in Phase B. This is the default until then.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_MARKETPLACE_URL = "http://localhost:3220"
DEFAULT_PUBSUB_TOPIC = "proofpay-delivery"
DEFAULT_AGENT_ID = "1"
DEFAULT_FIRESTORE_DATABASE = "(default)"
# How long a mission may sit in AWAITING_DELIVERY before a sweep treats the
# provider as non-delivering and runs the release gate anyway (P1 then fails →
# DISPUTE). Generous by default so a sweep that races the provider's work window
# does not dispute a healthy mission; demo_fraud.sh overrides it low.
DEFAULT_DELIVERY_DEADLINE_SECONDS = 86_400

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean-like value, got {raw!r}")


class Settings(BaseModel):
    """Immutable snapshot of the process environment.

    Load with :meth:`from_env`. Nothing in the package should construct this by
    hand except tests.
    """

    model_config = ConfigDict(frozen=True)

    # Google Cloud (SPEC R3).
    google_cloud_project: str = ""
    firestore_database: str = DEFAULT_FIRESTORE_DATABASE

    # Gemini / judgment (SPEC R1, R2, §7 stub).
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_api_key: str = ""
    judge_stub: bool = True

    # Pacta marketplace + MCP wiring (SPEC §2.2).
    marketplace_url: str = DEFAULT_MARKETPLACE_URL
    agent_id: str = DEFAULT_AGENT_ID

    # Events + demo auth (SPEC §2.2, §2.3).
    pubsub_topic: str = DEFAULT_PUBSUB_TOPIC
    demo_token: str = ""

    # Orchestration (SPEC §2.2 sweep fallback).
    delivery_deadline_seconds: int = DEFAULT_DELIVERY_DEADLINE_SECONDS

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
            firestore_database=os.environ.get(
                "FIRESTORE_DATABASE", DEFAULT_FIRESTORE_DATABASE
            ),
            gemini_model=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            judge_stub=_env_bool("JUDGE_STUB", True),
            marketplace_url=os.environ.get(
                "MARKETPLACE_URL", DEFAULT_MARKETPLACE_URL
            ),
            agent_id=os.environ.get("AGENT_ID", DEFAULT_AGENT_ID),
            pubsub_topic=os.environ.get("PUBSUB_TOPIC", DEFAULT_PUBSUB_TOPIC),
            demo_token=os.environ.get("DEMO_TOKEN", ""),
            delivery_deadline_seconds=int(
                os.environ.get(
                    "DELIVERY_DEADLINE_SECONDS",
                    str(DEFAULT_DELIVERY_DEADLINE_SECONDS),
                )
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once from the environment."""
    return Settings.from_env()
