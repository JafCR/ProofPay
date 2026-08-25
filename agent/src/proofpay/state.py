"""Mission repository with an enforced state machine (SPEC §4).

Two backends sit behind one interface, :class:`MissionRepository`:

- :class:`InMemoryRepository` for local development and tests (no network).
- :class:`FirestoreRepository` for Cloud Run, which imports
  ``google-cloud-firestore`` lazily so the base install stays offline.

The status lifecycle is ``CREATED -> CONTRACTED -> FUNDED -> AWAITING_DELIVERY
-> VERIFYING -> RELEASED | DISPUTED``. Every status change goes through
:func:`assert_transition`; an illegal transition raises :class:`IllegalTransition`
and never mutates state. Wakes are persisted per mission as the
``missions/{id}/wakes/{wake_id}`` subcollection (SPEC §4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Mission, MissionStatus, MissionTrace, WakeCycle, utcnow

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class StateError(Exception):
    """Base class for repository / state-machine errors."""


class MissionNotFound(StateError):
    def __init__(self, mission_id: str) -> None:
        super().__init__(f"mission {mission_id!r} not found")
        self.mission_id = mission_id


class MissionAlreadyExists(StateError):
    def __init__(self, mission_id: str) -> None:
        super().__init__(f"mission {mission_id!r} already exists")
        self.mission_id = mission_id


class IllegalTransition(StateError):
    """Raised when a status change is not allowed by the state machine."""

    def __init__(self, current: MissionStatus, target: MissionStatus) -> None:
        super().__init__(
            f"illegal transition {current.value} -> {target.value}"
        )
        self.current = current
        self.target = target


# --------------------------------------------------------------------------- #
# State machine (SPEC §4)
# --------------------------------------------------------------------------- #
#: Allowed next statuses for each status. RELEASED and DISPUTED are terminal.
#: VERIFYING -> AWAITING_DELIVERY supports a wake that fired before delivery
#: (a WAIT verdict) rolling back to wait for the real delivery event.
LEGAL_TRANSITIONS: dict[MissionStatus, frozenset[MissionStatus]] = {
    MissionStatus.CREATED: frozenset({MissionStatus.CONTRACTED}),
    MissionStatus.CONTRACTED: frozenset({MissionStatus.FUNDED}),
    MissionStatus.FUNDED: frozenset({MissionStatus.AWAITING_DELIVERY}),
    MissionStatus.AWAITING_DELIVERY: frozenset({MissionStatus.VERIFYING}),
    MissionStatus.VERIFYING: frozenset(
        {
            MissionStatus.RELEASED,
            MissionStatus.DISPUTED,
            MissionStatus.AWAITING_DELIVERY,
        }
    ),
    MissionStatus.RELEASED: frozenset(),
    MissionStatus.DISPUTED: frozenset(),
}


def is_legal_transition(current: MissionStatus, target: MissionStatus) -> bool:
    return target in LEGAL_TRANSITIONS[current]


def assert_transition(current: MissionStatus, target: MissionStatus) -> None:
    """Raise :class:`IllegalTransition` unless ``current -> target`` is allowed."""
    if not is_legal_transition(current, target):
        raise IllegalTransition(current, target)


# --------------------------------------------------------------------------- #
# Repository interface
# --------------------------------------------------------------------------- #
class MissionRepository(ABC):
    """Persistence for missions and their wake subcollection."""

    @abstractmethod
    def create_mission(self, mission: Mission) -> Mission:
        """Persist a new mission. Raise :class:`MissionAlreadyExists` on clash."""

    @abstractmethod
    def get_mission(self, mission_id: str) -> Mission:
        """Return a mission or raise :class:`MissionNotFound`."""

    @abstractmethod
    def update_status(self, mission_id: str, target: MissionStatus) -> Mission:
        """Transition a mission's status, enforcing the state machine."""

    @abstractmethod
    def patch_mission(self, mission_id: str, **fields: object) -> Mission:
        """Update non-status mission fields (offer_id, selection, ...)."""

    @abstractmethod
    def add_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        """Append a wake to the mission's subcollection."""

    @abstractmethod
    def save_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        """Upsert a wake (same ``wake_id``) as it accrues checks and a verdict."""

    @abstractmethod
    def get_wakes(self, mission_id: str) -> list[WakeCycle]:
        """Return the mission's wakes in insertion order."""

    @abstractmethod
    def list_missions(
        self, status: MissionStatus | None = None
    ) -> list[Mission]:
        """All missions, optionally filtered by status.

        Used by ``POST /sweep`` to find missions stuck in ``AWAITING_DELIVERY``
        and by the delivery handler to resolve a mission from an engagement id
        when the event carries no ``mission_id`` (SPEC §2.2).
        """

    def get_trace(self, mission_id: str) -> MissionTrace:
        """Mission plus its wakes - the shape ``GET /missions/{id}`` returns."""
        return MissionTrace(
            mission=self.get_mission(mission_id),
            wakes=self.get_wakes(mission_id),
        )


def _reject_status_field(fields: dict) -> None:
    if "status" in fields:
        raise ValueError(
            "use update_status() to change status; patch_mission() is for other fields"
        )


# --------------------------------------------------------------------------- #
# In-memory backend (tests / local)
# --------------------------------------------------------------------------- #
class InMemoryRepository(MissionRepository):
    """A dict-backed repository. Stores deep copies so callers cannot mutate
    persisted state by holding onto a reference."""

    def __init__(self) -> None:
        self._missions: dict[str, Mission] = {}
        self._wakes: dict[str, list[WakeCycle]] = {}

    def create_mission(self, mission: Mission) -> Mission:
        if mission.mission_id in self._missions:
            raise MissionAlreadyExists(mission.mission_id)
        stored = mission.model_copy(deep=True)
        self._missions[mission.mission_id] = stored
        self._wakes.setdefault(mission.mission_id, [])
        return stored.model_copy(deep=True)

    def get_mission(self, mission_id: str) -> Mission:
        try:
            return self._missions[mission_id].model_copy(deep=True)
        except KeyError:
            raise MissionNotFound(mission_id) from None

    def update_status(self, mission_id: str, target: MissionStatus) -> Mission:
        current = self._missions.get(mission_id)
        if current is None:
            raise MissionNotFound(mission_id)
        assert_transition(current.status, target)
        updated = current.model_copy(
            update={"status": target, "updated_at": utcnow()}
        )
        self._missions[mission_id] = updated
        return updated.model_copy(deep=True)

    def patch_mission(self, mission_id: str, **fields: object) -> Mission:
        _reject_status_field(fields)
        current = self._missions.get(mission_id)
        if current is None:
            raise MissionNotFound(mission_id)
        updated = current.model_copy(
            update={**fields, "updated_at": utcnow()}
        )
        self._missions[mission_id] = updated
        return updated.model_copy(deep=True)

    def add_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        if mission_id not in self._missions:
            raise MissionNotFound(mission_id)
        self._wakes.setdefault(mission_id, []).append(wake.model_copy(deep=True))
        return wake.model_copy(deep=True)

    def save_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        if mission_id not in self._missions:
            raise MissionNotFound(mission_id)
        wakes = self._wakes.setdefault(mission_id, [])
        for i, existing in enumerate(wakes):
            if existing.wake_id == wake.wake_id:
                wakes[i] = wake.model_copy(deep=True)
                break
        else:
            wakes.append(wake.model_copy(deep=True))
        return wake.model_copy(deep=True)

    def get_wakes(self, mission_id: str) -> list[WakeCycle]:
        if mission_id not in self._missions:
            raise MissionNotFound(mission_id)
        return [w.model_copy(deep=True) for w in self._wakes.get(mission_id, [])]

    def list_missions(
        self, status: MissionStatus | None = None
    ) -> list[Mission]:
        missions = [m.model_copy(deep=True) for m in self._missions.values()]
        if status is not None:
            missions = [m for m in missions if m.status == status]
        return missions


# --------------------------------------------------------------------------- #
# Firestore backend (Cloud Run)
# --------------------------------------------------------------------------- #
class FirestoreRepository(MissionRepository):
    """Firestore-backed repository (SPEC §4).

    ``google-cloud-firestore`` is imported lazily in ``__init__`` so importing
    ``proofpay.state`` never requires the cloud SDK. Status transitions run in a
    Firestore transaction so a concurrent wake cannot skip a state
    (SPEC §8: transactions on status transitions).
    """

    def __init__(
        self,
        project: str | None = None,
        database: str = "(default)",
        client: object | None = None,
    ) -> None:
        if client is None:
            from google.cloud import firestore  # lazy: cloud-only dependency

            client = firestore.Client(project=project, database=database)
        self._client = client
        self._missions = client.collection("missions")

    def _doc(self, mission_id: str):
        return self._missions.document(mission_id)

    def create_mission(self, mission: Mission) -> Mission:
        doc = self._doc(mission.mission_id)
        if doc.get().exists:
            raise MissionAlreadyExists(mission.mission_id)
        doc.set(mission.model_dump(mode="json"))
        return mission

    def get_mission(self, mission_id: str) -> Mission:
        snap = self._doc(mission_id).get()
        if not snap.exists:
            raise MissionNotFound(mission_id)
        return Mission.model_validate(snap.to_dict())

    def update_status(self, mission_id: str, target: MissionStatus) -> Mission:
        from google.cloud import firestore  # lazy

        doc = self._doc(mission_id)

        @firestore.transactional
        def _txn(transaction) -> Mission:
            snap = doc.get(transaction=transaction)
            if not snap.exists:
                raise MissionNotFound(mission_id)
            current = Mission.model_validate(snap.to_dict())
            assert_transition(current.status, target)
            updated = current.model_copy(
                update={"status": target, "updated_at": utcnow()}
            )
            transaction.set(doc, updated.model_dump(mode="json"))
            return updated

        return _txn(self._client.transaction())

    def patch_mission(self, mission_id: str, **fields: object) -> Mission:
        _reject_status_field(fields)
        doc = self._doc(mission_id)
        snap = doc.get()
        if not snap.exists:
            raise MissionNotFound(mission_id)
        current = Mission.model_validate(snap.to_dict())
        updated = current.model_copy(update={**fields, "updated_at": utcnow()})
        doc.set(updated.model_dump(mode="json"))
        return updated

    def add_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        if not self._doc(mission_id).get().exists:
            raise MissionNotFound(mission_id)
        self._doc(mission_id).collection("wakes").document(wake.wake_id).set(
            wake.model_dump(mode="json")
        )
        return wake

    def save_wake(self, mission_id: str, wake: WakeCycle) -> WakeCycle:
        # Firestore set() is an upsert keyed by wake_id, matching add_wake.
        return self.add_wake(mission_id, wake)

    def get_wakes(self, mission_id: str) -> list[WakeCycle]:
        if not self._doc(mission_id).get().exists:
            raise MissionNotFound(mission_id)
        docs = (
            self._doc(mission_id)
            .collection("wakes")
            .order_by("started_at")
            .stream()
        )
        return [WakeCycle.model_validate(d.to_dict()) for d in docs]

    def list_missions(
        self, status: MissionStatus | None = None
    ) -> list[Mission]:
        query = self._missions
        if status is not None:
            from google.cloud.firestore_v1.base_query import FieldFilter  # lazy

            query = query.where(
                filter=FieldFilter("status", "==", status.value)
            )
        return [Mission.model_validate(d.to_dict()) for d in query.stream()]


__all__ = [
    "StateError",
    "MissionNotFound",
    "MissionAlreadyExists",
    "IllegalTransition",
    "LEGAL_TRANSITIONS",
    "is_legal_transition",
    "assert_transition",
    "MissionRepository",
    "InMemoryRepository",
    "FirestoreRepository",
]
