"""Delivery-event parsing tests (SPEC §2.2), no network."""

from __future__ import annotations

import base64
import json

import pytest

from proofpay.events import DeliveryEvent, EventParseError, parse_delivery_event


def _pubsub_envelope(payload: dict, message_id: str = "m-1") -> dict:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": data, "messageId": message_id}, "subscription": "sub"}


def test_parses_pubsub_push_envelope():
    event = parse_delivery_event(
        _pubsub_envelope({"engagement_id": "eng-1", "mission_id": "mis-1"})
    )
    assert event == DeliveryEvent(
        engagement_id="eng-1", mission_id="mis-1", message_id="m-1"
    )


def test_parses_flat_local_payload():
    event = parse_delivery_event({"engagement_id": "eng-9", "mission_id": "mis-9"})
    assert event.engagement_id == "eng-9"
    assert event.mission_id == "mis-9"
    assert event.message_id is None


def test_engagement_only_event_is_valid():
    event = parse_delivery_event({"engagement_id": "eng-1"})
    assert event.engagement_id == "eng-1"
    assert event.mission_id is None
    assert event.resolves_a_mission()


def test_pubsub_attributes_used_when_no_data():
    body = {"message": {"attributes": {"engagement_id": "eng-2"}, "messageId": "x"}}
    event = parse_delivery_event(body)
    assert event.engagement_id == "eng-2"


def test_numeric_ids_are_coerced_to_str():
    event = parse_delivery_event({"engagement_id": 7, "mission_id": 42})
    assert event.engagement_id == "7"
    assert event.mission_id == "42"


def test_empty_event_raises():
    with pytest.raises(EventParseError):
        parse_delivery_event({"foo": "bar"})


def test_bad_base64_raises():
    with pytest.raises(EventParseError):
        parse_delivery_event({"message": {"data": "!!!not base64!!!"}})


def test_non_json_data_raises():
    data = base64.b64encode(b"not json").decode()
    with pytest.raises(EventParseError):
        parse_delivery_event({"message": {"data": data}})


def test_non_object_body_raises():
    with pytest.raises(EventParseError):
        parse_delivery_event(["not", "an", "object"])  # type: ignore[arg-type]
