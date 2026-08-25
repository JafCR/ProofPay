"""Environment configuration for ProofPay (SPEC §5).

All configuration is read from environment variables here and nowhere else; the
rest of the codebase imports :class:`Settings` and never touches ``os.environ``
or hard-codes a URL, model name, or token. Defaults are chosen so the package
runs locally with no cloud project and no API key (``JUDGE_STUB`` on).
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, field_validator

# Requirement from SPEC §5 / R1: the pinned model string is verified against the
# live Gemini model list in Phase B. This is the default until then.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_MARKETPLACE_URL = "http://localhost:3220"
DEFAULT_PUBSUB_TOPIC = "proofpay-delivery"
DEFAULT_AGENT_ID = "1"
DEFAULT_FIRESTORE_DATABASE = "(default)"
# Vertex AI region for the Gemini judge (DECISIONS 2026-08-25: Vertex + ADC, no
# API key). `global` is the endpoint verified against the live model list.
DEFAULT_GOOGLE_CLOUD_LOCATION = "global"
# Which mission store to use. `auto` keeps the historical rule (Firestore when a
# GCP project is configured, else in-memory); `memory`/`firestore` force one —
# `memory` lets a run set GOOGLE_CLOUD_PROJECT (needed for the Vertex judge)
# without dragging Firestore into a local demo.
DEFAULT_STATE_BACKEND = "auto"
_STATE_BACKENDS = {"auto", "memory", "firestore"}
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
    google_cloud_location: str = DEFAULT_GOOGLE_CLOUD_LOCATION
    firestore_database: str = DEFAULT_FIRESTORE_DATABASE
    # Mission store selector: auto | memory | firestore.
    state_backend: str = DEFAULT_STATE_BACKEND

    # Gemini / judgment (SPEC R1, R2, §7 stub).
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_api_key: str = ""
    judge_stub: bool = True

    @field_validator("state_backend")
    @classmethod
    def _check_state_backend(cls, v: str) -> str:
        if v not in _STATE_BACKENDS:
            raise ValueError(
                f"state_backend must be one of {sorted(_STATE_BACKENDS)}, got {v!r}"
            )
        return v

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
            google_cloud_location=os.environ.get(
                "GOOGLE_CLOUD_LOCATION", DEFAULT_GOOGLE_CLOUD_LOCATION
            ),
            firestore_database=os.environ.get(
                "FIRESTORE_DATABASE", DEFAULT_FIRESTORE_DATABASE
            ),
            state_backend=os.environ.get("STATE_BACKEND", DEFAULT_STATE_BACKEND),
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
