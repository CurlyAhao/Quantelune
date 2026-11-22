"""Pure completion prefix: all SDK entries terminate, including actual death coverage."""

from dataclasses import dataclass
from typing import Any

from app.research.contracts import logical_hash, parse_document
from app.research.reviewer_completion_contracts import Finance, SDKTerminal
from app.research.reviewer_identity import ReviewerClaim
from app.research.reviewer_recovery_contracts import Producer, RecoveryRecord, producer
from app.research.reviewer_sdk_events import ENTERED, QUIESCED, Entry, Quiesced


@dataclass(frozen=True)
class Prefix:
    root: Any
    claim: ReviewerClaim
    epoch: Producer
    recovery: Any
    terminals: tuple[SDKTerminal, ...]
    finances: tuple[Finance, ...]


def collect(events, owner, scope_id, run, attempt, binding, horizon, head, observed_at_ms):
    """Caller proves the chain, actual authorities and event-time control projection."""
    from app.research.reviewer_recovery import successor

    if (
        not events
        or len(events) > 10000
        or (events[-1].sequence, events[-1].chain_hash) != (horizon, head)
    ):
        raise ValueError("Completion horizon is not its actual complete prefix.")
    root = claim = epoch = recovery = active = None
    entries: dict[str, tuple[Any, Entry]] = {}
    terminals: dict[str, SDKTerminal] = {}
    charges: dict[str, tuple[Any, dict]] = {}
    by_id = {str(row.id): row for row in events}
    for index, event in enumerate(events, 1):
        value = parse_document(event.payload_json)
        if (
            event.sequence != index
            or event.workspace_id != scope_id
            or (event.logical_hash != logical_hash(value))
        ):
            raise ValueError("Completion prefix is incomplete or edited.")
        kind = event.event_type
        if kind in {"reviewer.claimed", "reviewer.recovered", ENTERED, QUIESCED} and (
            event.entity_id == run.id
        ):
            if event.actor_id != owner or event.entity_kind != "run":
                raise ValueError("Completion producer event scope changed.")
            if kind == "reviewer.claimed":
                candidate = ReviewerClaim.model_validate(value)
                if (
                    root is not None
                    or candidate.owner_id != str(owner)
                    or candidate.run_id != str(run.id)
                    or candidate.attempt_id != str(attempt.id)
                    or candidate.input_hash != run.input_hash
                ):
                    raise ValueError("Completion original producer identity changed.")
                root, claim, epoch = event, candidate, producer(candidate)
            elif root is None or claim is None or epoch is None:
                raise ValueError("Completion operation precedes its original claim.")
            elif kind == "reviewer.recovered":
                record = RecoveryRecord.model_validate(value)
                review = by_id.get(record.review_id)
                if (
                    record.observation.claim.attempt_id != str(attempt.id)
                    or record.observation.claim.run_id != str(run.id)
                    or review is None
                    or review.sequence >= event.sequence
                    or review.event_type != "reviewer.review_receipted"
                    or review.entity_id != attempt.id
                    or review.actor_id != owner
                    or review.logical_hash != record.review_hash
                    or record.binding_id != str(binding.id)
                    or record.binding_hash != binding.binding_hash
                    or record.recovered_at_ms > observed_at_ms
                    or record.phase != "review_only"
                ):
                    raise ValueError("Completion recovery has no strictly prior actual Review.")
                after = successor(record, root, claim, epoch, recovery)
                if active is not None:
                    entered_event, entered = entries[active]
                    if entered.producer != record.observation.producer or (
                        record.observation.observed_at_ms < entered.entered_at_ms
                    ):
                        raise ValueError("Death coverage belongs to another SDK producer.")
                    terminals[active] = SDKTerminal(
                        invocation_id=active,
                        entry_event_id=str(entered_event.id),
                        entry_hash=entered_event.logical_hash,
                        terminal_kind="recovered",
                        terminal_event_id=str(event.id),
                        terminal_hash=event.logical_hash,
                    )
                    active = None
                epoch, recovery = after, event
            elif kind == ENTERED:
                entered = Entry.model_validate(value)
                if (
                    active is not None
                    or entered.invocation_id in entries
                    or entered.owner_id != str(owner)
                    or entered.run_id != str(run.id)
                    or entered.attempt_id != str(attempt.id)
                    or entered.input_hash != run.input_hash
                    or entered.claim_event_id != str(root.id)
                    or entered.claim_hash != root.logical_hash
                    or entered.producer != epoch
                    or entered.producer != producer(claim)
                    or entered.binding_id != str(binding.id)
                    or entered.binding_hash != binding.binding_hash
                    or entered.thread_id != binding.thread_id
                    or entered.entered_at_ms > observed_at_ms
                ):
                    raise ValueError("Completion SDK entry identity or lifetime changed.")
                entries[entered.invocation_id] = (event, entered)
                active = entered.invocation_id
            else:
                closed = Quiesced.model_validate(value)
                if active != closed.invocation_id or active not in entries or active in terminals:
                    raise ValueError("Completion SDK quiescence has no unique live entry.")
                entered_event, entered = entries[active]
                if (
                    closed.owner_id != str(owner)
                    or closed.run_id != str(run.id)
                    or closed.attempt_id != str(attempt.id)
                    or closed.producer != epoch
                    or closed.entry_event_id != str(entered_event.id)
                    or closed.entry_hash != entered_event.logical_hash
                    or not entered.entered_at_ms <= closed.observed_at_ms <= observed_at_ms
                ):
                    raise ValueError("Completion SDK quiescence scope/time changed.")
                terminals[active] = SDKTerminal(
                    invocation_id=active,
                    entry_event_id=str(entered_event.id),
                    entry_hash=entered_event.logical_hash,
                    terminal_kind="quiesced",
                    terminal_event_id=str(event.id),
                    terminal_hash=event.logical_hash,
                )
                active = None
        elif kind in {
            "harness.model_request",
            "reviewer.tool_recorded",
            "reviewer.review_receipted",
        } and (value.get("run_id") == str(run.id)):
            if (
                active is None
                or event.actor_id != owner
                or (
                    value.get("attempt_id") != str(attempt.id)
                    or value.get("binding_hash") != binding.binding_hash
                )
            ):
                raise ValueError("Completion operation has no active SDK interval.")
        elif kind == "security.charge" and value.get("run_id") == str(run.id):
            if (
                active is None
                or event.actor_id != owner
                or event.entity_kind != "security_charge"
                or value.get("owner_id") != str(owner)
                or value.get("id") != str(event.entity_id)
                or value.get("state")
                not in {"reserved", "dispatched", "unknown", "settled", "released"}
            ):
                raise ValueError("Completion finance event has no owned SDK interval.")
            charges[str(event.entity_id)] = (event, value)
    if (
        root is None
        or claim is None
        or epoch is None
        or active is not None
        or not entries
        or set(entries) != set(terminals)
        or len(entries) > 100
        or not charges
        or len(charges) > 100
    ):
        raise ValueError("Completion SDK/financial set is open, absent or unbounded.")
    return Prefix(
        root,
        claim,
        epoch,
        recovery,
        tuple(terminals[key] for key in entries),
        tuple(
            Finance(
                charge_id=key,
                event_id=str(event.id),
                event_hash=event.logical_hash,
                state=value["state"],
            )
            for key, (event, value) in sorted(charges.items())
        ),
    )
