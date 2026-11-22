"""Pure as-of references; caller must separately prove the original ledger chain."""

from typing import Any

from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_boundary_contracts import ChargeRef, Pair
from app.research.reviewer_sdk_events import ENTERED, QUIESCED, Entry, Quiesced


def collect(
    events,
    owner,
    scope_id,
    run_id,
    attempt_id,
    binding_hash,
    horizon_sequence,
    horizon_hash,
    observed_at_ms,
):
    """No DB/OS/transport or authority grant. Only exact earlier event references."""
    if (
        not events
        or len(events) > 10000
        or (events[-1].sequence != horizon_sequence or events[-1].chain_hash != horizon_hash)
    ):
        raise ValueError("Boundary horizon is not the actual preceding event.")
    entries: dict[str, tuple[Any, Entry]] = {}
    closed: dict[str, Any] = {}
    charges: dict[str, tuple[Any, dict]] = {}
    active = None
    for index, event in enumerate(events, start=1):
        value = parse_document(event.payload_json)
        if (
            event.sequence != index
            or event.workspace_id != scope_id
            or event.logical_hash != logical_hash(value)
        ):
            raise ValueError("Boundary prefix is incomplete or changed.")
        kind = event.event_type
        if kind == "harness.thread_bound" and event.entity_id == run_id and binding_hash is None:
            raise ValueError("Pre-start boundary already has a thread binding.")
        if kind in {ENTERED, QUIESCED} and event.entity_id == run_id:
            record = (
                Entry.model_validate(value) if kind == ENTERED else Quiesced.model_validate(value)
            )
            if (
                event.actor_id != owner
                or event.entity_kind != "run"
                or record.owner_id != str(owner)
                or record.run_id != str(run_id)
                or record.attempt_id != str(attempt_id)
            ):
                raise ValueError("Boundary SDK scope changed.")
            if isinstance(record, Entry):
                if (
                    active is not None
                    or record.invocation_id in entries
                    or record.binding_hash != binding_hash
                ):
                    raise ValueError("Boundary SDK entry overlaps or changes binding.")
                entries[record.invocation_id] = (event, record)
                active = record.invocation_id
            else:
                previous = entries.get(record.invocation_id)
                if (
                    previous is None
                    or active != record.invocation_id
                    or record.invocation_id in closed
                ):
                    raise ValueError("Boundary SDK quiescence has no unique prior entry.")
                entered_event, entered = previous
                if (
                    record.entry_event_id != str(entered_event.id)
                    or record.entry_hash != entered_event.logical_hash
                    or record.producer != entered.producer
                    or record.observed_at_ms < entered.entered_at_ms
                    or record.observed_at_ms > observed_at_ms
                ):
                    raise ValueError("Boundary SDK quiescence identity/time changed.")
                closed[record.invocation_id] = event
                active = None
        elif kind in {
            "harness.model_request",
            "reviewer.tool_recorded",
            "reviewer.review_receipted",
        } and value.get("run_id") == str(run_id):
            if (
                active is None
                or event.actor_id != owner
                or value.get("attempt_id") != str(attempt_id)
                or value.get("binding_hash") != binding_hash
            ):
                raise ValueError("Boundary cannot backfill an untracked model/tool operation.")
        elif kind == "security.charge" and value.get("run_id") == str(run_id):
            if (
                active is None
                or event.actor_id != owner
                or event.entity_kind != "security_charge"
                or value.get("owner_id") != str(owner)
                or value.get("id") != str(event.entity_id)
                or value.get("state")
                not in {"reserved", "dispatched", "unknown", "settled", "released"}
            ):
                raise ValueError("Boundary financial event identity changed.")
            charges[str(event.entity_id)] = (event, value)
    if (
        active is not None
        or set(entries) != set(closed)
        or len(entries) > 100
        or len(charges) > 100
    ):
        raise ValueError("Boundary has an unquiesced or unbounded SDK/financial set.")
    pairs = tuple(
        Pair(
            invocation_id=key,
            entry_event_id=str(event.id),
            entry_hash=event.logical_hash,
            quiesced_event_id=str(closed[key].id),
            quiesced_hash=closed[key].logical_hash,
        )
        for key, (event, _) in entries.items()
    )
    finances = tuple(
        ChargeRef(
            charge_id=key,
            event_id=str(event.id),
            event_hash=event.logical_hash,
            state=value["state"],
        )
        for key, (event, value) in sorted(charges.items())
    )
    return pairs, finances
