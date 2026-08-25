"""STATE_BACKEND selection + the new Vertex/location settings (Phase B, task #16).

Fully offline: the Firestore path is monkeypatched so no cloud SDK or network is
touched. Covers the load-bearing case — a run that sets GOOGLE_CLOUD_PROJECT (so
the Vertex judge works) but forces the in-memory store via STATE_BACKEND=memory.
"""

from __future__ import annotations

import pytest

from proofpay import main
from proofpay.main import _build_repository
from proofpay.settings import Settings
from proofpay.state import InMemoryRepository


class _FakeFirestore:
    """Stand-in for FirestoreRepository that records how it was built."""

    def __init__(self, project=None, database="(default)", client=None):
        self.project = project
        self.database = database


@pytest.fixture
def fake_firestore(monkeypatch):
    monkeypatch.setattr(main, "FirestoreRepository", _FakeFirestore)
    return _FakeFirestore


# --------------------------------------------------------------------------- #
# state_backend selection
# --------------------------------------------------------------------------- #
def test_memory_forced_even_with_project(fake_firestore):
    # The Vertex judge needs a project set; memory must still win.
    s = Settings(state_backend="memory", google_cloud_project="proj-x")
    assert isinstance(_build_repository(s), InMemoryRepository)


def test_auto_without_project_is_memory(fake_firestore):
    s = Settings(state_backend="auto", google_cloud_project="")
    assert isinstance(_build_repository(s), InMemoryRepository)


def test_auto_with_project_is_firestore(fake_firestore):
    s = Settings(state_backend="auto", google_cloud_project="proj-x")
    repo = _build_repository(s)
    assert isinstance(repo, _FakeFirestore)
    assert repo.project == "proj-x"


def test_firestore_forced_without_project(fake_firestore):
    s = Settings(state_backend="firestore")
    repo = _build_repository(s)
    assert isinstance(repo, _FakeFirestore)
    assert repo.project is None


# --------------------------------------------------------------------------- #
# settings fields + validation
# --------------------------------------------------------------------------- #
def test_location_default_is_global():
    assert Settings().google_cloud_location == "global"


def test_state_backend_default_is_auto():
    assert Settings().state_backend == "auto"


def test_bad_state_backend_rejected():
    with pytest.raises(ValueError):
        Settings(state_backend="sqlite")


def test_from_env_reads_new_vars(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-y")
    s = Settings.from_env()
    assert s.google_cloud_location == "us-central1"
    assert s.state_backend == "memory"
    assert s.google_cloud_project == "proj-y"
