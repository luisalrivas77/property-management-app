"""The History timeline: milestones of a property or unit (tests/fixtures/phase1/timeline.yaml).

History and the audit trail are the same event log (principle 4). The audit trail is every
event in seq order; History is milestones only, with each correction chain collapsed into the
event that is true now.
"""

from collections.abc import Sequence
from uuid import UUID

from hausbuch.schemas.events import Event, TimelineEntry, TimelineScope


def build_history(events: Sequence[Event], scope: TimelineScope) -> list[TimelineEntry]:
    """Milestones in scope, one entry per correction chain, ordered by (date, root seq).

    `events` must hold every event of each chain it touches (the workspace's log is simplest):
    chains are resolved over all of them before scoping. A chain A -> B -> C is shown as C, so
    C's scope, date and payload decide where and when it appears; root_event_id and
    original_effective_date come from A. Events without an effective_date are not milestones.
    The result does not depend on the order of `events`.
    """
    roots, successor = _chains(events)
    keyed: list[tuple[tuple, TimelineEntry]] = []
    for root in roots:
        latest = root
        while latest.id in successor:
            latest = successor[latest.id]
        if latest.effective_date is None or not _in_scope(latest, scope):
            continue
        corrected = latest.id != root.id
        entry = TimelineEntry(
            event_id=latest.id,
            root_event_id=root.id,
            type=latest.type,
            entity_type=latest.entity_type,
            entity_id=latest.entity_id,
            effective_date=latest.effective_date,
            corrected=corrected,
            original_effective_date=root.effective_date if corrected else None,
            payload=latest.payload,
        )
        # The root's seq keeps a corrected move-in ahead of the rent row recorded after it.
        keyed.append(((latest.effective_date, root.seq, root.id), entry))
    keyed.sort(key=lambda pair: pair[0])
    return [entry for _, entry in keyed]


def _chains(events: Sequence[Event]) -> tuple[list[Event], dict[UUID, Event]]:
    """Chain roots, and for each corrected event the event that corrects it.

    Raises ValueError on a log the database could not hold: duplicate ids, a correction of an
    event that is missing, an event corrected twice, or a cycle.
    """
    by_id: dict[UUID, Event] = {}
    for e in events:
        if e.id in by_id:
            raise ValueError(f"event {e.id} appears twice")
        by_id[e.id] = e
    successor: dict[UUID, Event] = {}
    for e in events:
        target = e.corrects_event_id
        if target is None:
            continue
        if target not in by_id:
            raise ValueError(f"event {e.id} corrects {target}, which is not in the input")
        if target in successor:
            raise ValueError(f"event {target} is corrected more than once")
        successor[target] = e
    roots = [e for e in events if e.corrects_event_id is None]
    in_chains = len(roots)
    for root in roots:
        e = root
        while e.id in successor:
            e = successor[e.id]
            in_chains += 1
    if in_chains != len(events):
        raise ValueError("correction cycle: some events have no uncorrected root")
    return roots, successor


def _in_scope(event: Event, scope: TimelineScope) -> bool:
    if event.property_id != scope.property_id:
        return False
    # A unit's history also shows property-level events (unit_id null): purchase, loans.
    return scope.unit_id is None or event.unit_id is None or event.unit_id == scope.unit_id
