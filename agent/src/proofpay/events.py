"""Delivery-event parsing (SPEC §2.2 POST /events/delivery).

The provider-bot publishes ``{engagement_id, mission_id}`` to Pub/Sub when it
finishes an engagement (SPEC §2.3). Pub/Sub push wraps that JSON, base64-encoded,
in an envelope:

    {"message": {"data": "<base64>", "messageId": "...", "attributes": {...}},
     "subscription": "..."}

Locally (and in smoke tests) the same payload may be POSTed flat, without the
envelope. :func:`parse_delivery_event` accepts either and yields a
:class:`DeliveryEvent`. ``mission_id`` is optional: a Pub/Sub message keyed only
by ``engagement_id`` is valid, and the handler resolves the mission from the
engagement (SPEC §2.2). At least one of the two ids must be present.
"""

from __future__ import annotations

import base64
import binascii
import json

from pydantic import BaseModel, ConfigDict


class EventParseError(ValueError):
    """The request body is not a delivery event this service understands."""


class DeliveryEvent(BaseModel):
    """A provider delivery signal. Carries an engagement id and/or a mission id."""

    model_config = ConfigDict(extra="ignore")

    engagement_id: str | None = None
    mission_id: str | None = None
    message_id: str | None = None

    def resolves_a_mission(self) -> bool:
        return bool(self.mission_id or self.engagement_id)


def _decode_pubsub_data(data: str) -> dict:
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EventParseError(f"message.data is not valid base64: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EventParseError(
            f"message.data does not decode to JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise EventParseError("decoded message.data is not a JSON object")
    return payload


def parse_delivery_event(body: dict) -> DeliveryEvent:
    """Parse a Pub/Sub push envelope or a flat local payload into a
    :class:`DeliveryEvent`. Raises :class:`EventParseError` on anything that
    carries neither an ``engagement_id`` nor a ``mission_id``."""
    if not isinstance(body, dict):
        raise EventParseError("request body must be a JSON object")

    message_id: str | None = None
    if "message" in body and isinstance(body["message"], dict):
        # Pub/Sub push envelope.
        message = body["message"]
        message_id = message.get("messageId")
        data = message.get("data")
        if data is None:
            # A push with no data but ids in attributes is still usable.
            payload = dict(message.get("attributes") or {})
        else:
            if not isinstance(data, str):
                raise EventParseError("message.data must be a base64 string")
            payload = _decode_pubsub_data(data)
    else:
        # Flat local payload.
        payload = body

    event = DeliveryEvent(
        engagement_id=_as_str(payload.get("engagement_id")),
        mission_id=_as_str(payload.get("mission_id")),
        message_id=message_id,
    )
    if not event.resolves_a_mission():
        raise EventParseError(
            "delivery event carries neither engagement_id nor mission_id"
        )
    return event


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = ["DeliveryEvent", "EventParseError", "parse_delivery_event"]
