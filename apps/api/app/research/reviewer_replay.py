"""Independent Reviewer temporal event proof; never executes or repairs business work.

Derived only from the existing Reader temporal algorithm. Its independent role,
claim, receipt, profile, tool and recovery modules remain explicit authorities.
"""

import uuid
from typing import Any

from sqlalchemy import select

from app.models.research import ResearchAttempt, ResearchRun
from app.research import reviewer_sdk_events
from app.research.contracts import State, logical_hash, parse_document, require_transition
from app.research.reviewer_completion_replay import Timeline as CompletionTimeline

KINDS = frozenset(
    {
        "reviewer.claimed",
        "reviewer.recovered",
        "reviewer.death_stopped",
        "reviewer.control_requested",
        "reviewer.control_acknowledged",
        "reviewer.stopped",
        "reviewer.sdk_finished",
        "reviewer.review_receipted",
        "reviewer.tool_recorded",
        "reviewer.boundary_observed",
        "reviewer.completion_observed",
        "reviewer.completion_released",
        *reviewer_sdk_events.KINDS,
    }
)


class ReviewerReplay:
    def __init__(self):
        self.admissions: set[uuid.UUID] = set()
        self.controls: dict[uuid.UUID, list[dict]] = {}
        self.claims: dict[uuid.UUID, dict] = {}
        self.claim_events: dict[uuid.UUID, Any] = {}
        self.epochs: dict[uuid.UUID, dict] = {}
        self.recoveries: dict[uuid.UUID, Any] = {}
        self.stops: dict[uuid.UUID, dict] = {}
        self.deaths: dict[uuid.UUID, Any] = {}
        self.sdk_done: set[uuid.UUID] = set()
        self.control_acks: set[tuple[uuid.UUID, int]] = set()
        self.reviews: set[uuid.UUID] = set()
        self.sdk_tasks = reviewer_sdk_events.Timeline()
        self.boundaries: dict[uuid.UUID, tuple[Any, Any]] = {}
        self.boundary_stops: set[uuid.UUID] = set()
        self.boundary_acks: set[uuid.UUID] = set()
        self.stop_events: dict[uuid.UUID, Any] = {}
        self.completions = CompletionTimeline()

    def finalize(self):
        self.completions.finalize()
        if self.boundary_stops != {
            key
            for key, (_, value) in self.boundaries.items()
            if value.action in {"failed", "interrupted", "cancel"}
        } or self.boundary_acks != {
            key
            for key, (_, value) in self.boundaries.items()
            if value.action in {"cancel", "pause", "resume"}
        }:
            raise ValueError("Reviewer boundary and consumer sets differ.")
        if set(self.deaths) != {
            identifier
            for identifier, stop in self.stops.items()
            if stop["proof"] == "verified_death"
        }:
            raise ValueError("Reviewer death and release event sets differ.")
        for identifier, event in self.deaths.items():
            if self.stops[identifier]["state"] == "cancelled":
                record = parse_document(event.payload_json)
                if (identifier, record["control_index"]) not in self.control_acks:
                    raise ValueError("Reviewer death cancellation lacks its acknowledgement.")

    def consume_stop(self, event, payload, current):
        key = uuid.UUID(payload["boundary_event_id"])
        previous = self.boundaries.get(key)
        if previous is None or key in self.boundary_stops:
            raise ValueError("Reviewer stop has no unique prior boundary.")
        observed_event, observed = previous
        if (
            observed_event.logical_hash != payload["boundary_hash"]
            or observed_event.sequence + 1 != event.sequence
            or observed.run_id != str(event.entity_id)
            or observed.attempt_id != payload["attempt_id"]
            or observed.before_state != current["state"]
            or observed.before_revision != current["revision"]
            or observed.after_state != payload["state"]
            or observed.action
            != ("cancel" if payload["state"] == "cancelled" else payload["state"])
            or observed.observed_at_ms != payload["released_at_ms"]
        ):
            raise ValueError("Reviewer stop changed its observed boundary.")
        self.boundary_stops.add(key)

    def consume_ack(self, event, payload, current):
        key = uuid.UUID(payload["boundary_event_id"])
        previous = self.boundaries.get(key)
        if previous is None or key in self.boundary_acks:
            raise ValueError("Reviewer ACK has no unique prior boundary.")
        observed_event, observed = previous
        changed = int(observed.before_state != observed.after_state)
        offset = 3 if observed.action == "cancel" else 1 + changed
        if (
            observed_event.logical_hash != payload["boundary_hash"]
            or event.sequence != observed_event.sequence + offset
            or observed.run_id != str(event.entity_id)
            or observed.attempt_id != payload["attempt_id"]
            or observed.action != payload["action"]
            or observed.control_index != payload["control_index"]
            or observed.after_state != current["state"]
            or observed.before_revision + changed != current["revision"]
            or (observed.action == "cancel" and key not in self.boundary_stops)
        ):
            raise ValueError("Reviewer ACK changed its observed boundary.")
        self.boundary_acks.add(key)

    async def transition(self, db, identifier, current, payload, event=None):
        attempt = await db.scalar(
            select(ResearchAttempt).where(
                ResearchAttempt.run_id == identifier,
                ResearchAttempt.number == current["attempt_count"],
            )
        )
        if attempt is None:
            raise ValueError("Reviewer transition has no actual Attempt.")
        after = payload["state"]
        controls = self.controls.get(attempt.id, [])
        latest = controls[-1]["action"] if controls else None
        cancelled = any(item["action"] == "cancel" for item in controls)
        planned = current["attempts"][current["attempt_count"]]
        if (
            after in {"running", "waiting_approval", "paused", "completed"}
            and attempt.id not in self.claims
        ):
            raise ValueError("Reviewer active state has no independent claim.")
        if after == "running" and current["state"] == "paused" and latest != "resume":
            raise ValueError("Reviewer resume has no prior control.")
        if after == "paused" and (not planned["pause_requested"] or latest != "pause"):
            raise ValueError("Reviewer pause has no prior request.")
        if after == "paused" and not any(
            observed.attempt_id == str(attempt.id)
            and observed.action == "pause"
            and observed.before_state == current["state"]
            and observed.before_revision == current["revision"]
            and (event is None or observed_event.sequence + 1 == event.sequence)
            for observed_event, observed in self.boundaries.values()
        ):
            raise ValueError("Reviewer pause has no prior boundary observation.")
        if after in {"running", "waiting_approval", "completed"} and cancelled:
            raise ValueError("Reviewer transition crossed cancellation.")
        if after == "completed":
            from app.research import reviewer_receipt

            if attempt.id not in self.reviews or planned["pause_requested"]:
                raise ValueError("Reviewer completion precedes its real review.")
            self.completions.transition(event, attempt.id, payload)
            run = await db.get(ResearchRun, identifier)
            receipt = await reviewer_receipt.prove(
                db, uuid.UUID(self.claims[attempt.id]["owner_id"]), run, attempt
            )
            if (
                payload.get("domain_kind"),
                payload.get("domain_run_id"),
                payload.get("completion_hash"),
            ) != (
                "reviewer_receipt",
                str(receipt.id),
                receipt.document_hash,
            ):
                raise ValueError("Reviewer completion receipt changed.")
        if after == "cancel_requested" and not cancelled:
            raise ValueError("Reviewer cancellation has no prior request.")
        if after in {"failed", "interrupted", "cancelled"}:
            never_claimed = (
                after == "cancelled"
                and current["state"] == "queued"
                and attempt.id not in self.claims
            )
            if never_claimed:
                if latest != "cancel":
                    raise ValueError("Unclaimed Reviewer cancellation has no request.")
            elif attempt.id not in self.stops or self.stops[attempt.id]["state"] != after:
                raise ValueError("Reviewer stop precedes its release proof.")
            elif event is not None and event.sequence != self.stop_events[attempt.id].sequence + 1:
                raise ValueError("Reviewer transition is not adjacent to its release proof.")

    def final_attempt(self, owner, run, attempt, lease):
        from app.research.reviewer_recovery_contracts import Producer, producer

        if (lease is not None) != (attempt.id in self.claims):
            raise ValueError("Reviewer original claim and lease set changed.")
        if lease is None:
            if attempt.state not in {"queued", "cancelled"}:
                raise ValueError("Reviewer state lacks an owned lease.")
            return
        if lease.owner_id != owner or Producer.model_validate(self.epochs[attempt.id]) != producer(
            lease
        ):
            raise ValueError("Reviewer final producer epoch changed.")
        terminal = attempt.state in {"completed", "failed", "cancelled", "interrupted"}
        if (
            terminal
            and self.sdk_tasks.open_for(attempt.id)
            and self.stops.get(attempt.id, {}).get("proof") != "verified_death"
            and not (
                attempt.state == "completed"
                and self.completions.covered(attempt.id) == set(self.sdk_tasks.open_for(attempt.id))
            )
        ):
            raise ValueError("Reviewer terminal state has an unquiesced SDK invocation.")
        if (lease.active_owner_id is None) != terminal or (
            lease.released_at_ms is not None
        ) != terminal:
            raise ValueError("Reviewer final release state changed.")
        if not terminal and lease.active_owner_id != owner:
            raise ValueError("Reviewer active owner changed.")
        if (
            terminal
            and attempt.state != "completed"
            and (attempt.id not in self.stops or self.stops[attempt.id]["state"] != attempt.state)
        ):
            raise ValueError("Reviewer terminal stop proof missing.")
        self.completions.final(run, attempt, lease)

    async def handle(self, db, owner, event, payload, runs, require_live_session):
        kind, identifier = event.event_type, event.entity_id
        if (
            kind == "reviewer.stopped"
            and payload.get("proof") == "owned_boundary"
            and not {"boundary_event_id", "boundary_hash"} <= set(payload)
        ):
            raise ValueError("Reviewer owned boundary proof is absent.")
        reviewer_controls = self.controls
        reviewer_claims = self.claims
        reviewer_claim_events = self.claim_events
        reviewer_epochs = self.epochs
        reviewer_recoveries = self.recoveries
        reviewer_sdk_done = self.sdk_done
        reviewer_control_acks = self.control_acks
        reviewer_stops = self.stops
        reviewer_deaths = self.deaths
        reviewer_reviews = self.reviews
        if kind in {"reviewer.completion_observed", "reviewer.completion_released"}:
            from app.models.research_planning import ResearchPlanningLease
            from app.research import reviewer_completion

            current = runs[identifier]
            attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
            lease = await db.get(ResearchPlanningLease, attempt.id) if attempt else None
            if (
                current["kind"] != "reviewer"
                or attempt is None
                or lease is None
                or attempt.run_id != identifier
                or attempt.number != current["attempt_count"]
                or attempt.id not in self.claims
                or attempt.id not in self.reviews
                or attempt.id in self.deaths
                or attempt.id in self.stops
                or current["attempts"][current["attempt_count"]]["pause_requested"]
                or any(c["action"] == "cancel" for c in self.controls.get(attempt.id, []))
            ):
                raise ValueError("Completion has no active uncancelled Reviewer Review.")
            actual_run = await db.get(ResearchRun, identifier)
            if kind == "reviewer.completion_observed":
                observed = await reviewer_completion.prove_observation(
                    db, owner, actual_run, attempt, lease, event
                )
                self.completions.observe(
                    event,
                    observed,
                    self.epochs[attempt.id],
                    current,
                    self.sdk_tasks.open_for(attempt.id),
                )
            else:
                released = await reviewer_completion.prove_release(
                    db, owner, actual_run, attempt, lease, event
                )
                self.completions.release(event, released, self.epochs[attempt.id], current)
        elif kind == "reviewer.boundary_observed":
            from app.models.research_planning import ResearchPlanningLease
            from app.research import reviewer_boundary

            current = runs[identifier]
            attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
            lease = await db.get(ResearchPlanningLease, attempt.id) if attempt is not None else None
            if (
                current["kind"] != "reviewer"
                or attempt is None
                or lease is None
                or attempt.run_id != identifier
                or attempt.number != current["attempt_count"]
                or attempt.id not in self.claims
                or attempt.id in self.recoveries
                or attempt.id in self.deaths
                or attempt.id in self.stops
                or event.id in self.boundaries
            ):
                raise ValueError("Boundary has no original active Reviewer claim.")
            actual_run = await db.get(ResearchRun, identifier)
            observed = await reviewer_boundary.prove(db, owner, actual_run, attempt, lease, event)
            from app.research.reviewer_recovery_contracts import Producer

            if (
                observed.before_state != current["state"]
                or observed.before_revision != current["revision"]
                or observed.producer != Producer.model_validate(self.epochs[attempt.id])
                or self.sdk_tasks.open_for(attempt.id)
                or (observed.action != "cancel" and attempt.id in self.reviews)
            ):
                raise ValueError("Boundary precedes quiescence or changes state/revision.")
            reviewer_boundary.action(
                observed,
                self.controls.get(attempt.id, []),
                current["attempts"][current["attempt_count"]]["pause_requested"],
            )
            self.boundaries[event.id] = (event, observed)
        elif kind in reviewer_sdk_events.KINDS:
            from app.models.research_harness import ResearchHarnessThread
            from app.research.reviewer_identity import ReviewerClaim

            current = runs[identifier]
            run = await db.get(ResearchRun, identifier)
            attempt_id = uuid.UUID(payload["attempt_id"])
            attempt = await db.get(ResearchAttempt, attempt_id)
            require_live_session(uuid.UUID(current["session_id"]))
            if (
                attempt is None
                or run is None
                or current["kind"] != "reviewer"
                or attempt.run_id != identifier
                or attempt.number != current["attempt_count"]
                or attempt_id not in self.claims
                or attempt_id in self.deaths
                or attempt_id in self.stops
                or attempt_id in self.recoveries
            ):
                raise ValueError("SDK observation has no original active Reviewer claim.")
            if kind == reviewer_sdk_events.ENTERED:
                record = reviewer_sdk_events.Entry.model_validate(payload)
                thread = await db.get(ResearchHarnessThread, uuid.UUID(record.binding_id))
                if thread is None or thread.attempt_id != attempt_id:
                    raise ValueError("SDK observation binding is absent.")
                if current["attempts"][current["attempt_count"]]["pause_requested"] or any(
                    control["action"] == "cancel" for control in self.controls.get(attempt_id, [])
                ):
                    raise ValueError("SDK entry crossed a control boundary.")
                await reviewer_sdk_events.binding_before(db, owner, run, thread, event.sequence)
                self.sdk_tasks.entry(
                    event,
                    payload,
                    owner,
                    run,
                    thread,
                    self.claim_events[attempt_id],
                    ReviewerClaim.model_validate(self.claims[attempt_id]),
                    self.epochs[attempt_id],
                    current["state"],
                )
            else:
                self.sdk_tasks.quiesce(
                    event, payload, owner, self.epochs[attempt_id], current["state"]
                )
        elif kind == "reviewer.claimed":
            from app.research.reviewer_identity import ReviewerClaim

            reviewer_claim_value = ReviewerClaim.model_validate(payload)
            claim_attempt_id = uuid.UUID(reviewer_claim_value.attempt_id)
            claim_attempt = await db.get(ResearchAttempt, claim_attempt_id)
            current = runs[identifier]
            require_live_session(uuid.UUID(current["session_id"]))
            if (
                event.entity_kind != "run"
                or event.actor_id != owner
                or current["kind"] != "reviewer"
                or current["state"] != "queued"
                or reviewer_claim_value.owner_id != str(owner)
                or reviewer_claim_value.run_id != str(identifier)
                or reviewer_claim_value.input_hash != current["input_hash"]
                or claim_attempt is None
                or claim_attempt.run_id != identifier
                or claim_attempt.number != current["attempt_count"]
                or claim_attempt_id not in self.admissions
                or claim_attempt_id in reviewer_claims
            ):
                raise ValueError("Reviewer original claim temporal identity changed.")
            reviewer_claims[claim_attempt_id] = payload
            reviewer_claim_events[claim_attempt_id] = event
            from app.research.reviewer_recovery_contracts import producer as reviewer_epoch_producer

            reviewer_epochs[claim_attempt_id] = reviewer_epoch_producer(
                reviewer_claim_value
            ).model_dump(mode="json")
        elif kind == "reviewer.recovered":
            from app.models.research_harness import ResearchHarnessThread
            from app.research import reviewer_receipt, reviewer_recovery
            from app.research.reviewer_identity import ReviewerClaim
            from app.research.reviewer_recovery_contracts import Producer, RecoveryRecord

            reviewer_recovered = RecoveryRecord.model_validate(payload)
            recovered_attempt_id = uuid.UUID(reviewer_recovered.observation.claim.attempt_id)
            recovered_attempt = await db.get(ResearchAttempt, recovered_attempt_id)
            current = runs[identifier]
            if (
                event.entity_kind != "run"
                or event.actor_id != owner
                or current["kind"] != "reviewer"
                or reviewer_recovered.observation.claim.run_id != str(identifier)
                or reviewer_recovered.observation.claim.owner_id != str(owner)
                or recovered_attempt_id not in reviewer_claims
                or recovered_attempt_id not in reviewer_reviews
                or recovered_attempt_id in reviewer_sdk_done
                or recovered_attempt is None
                or recovered_attempt.run_id != identifier
                or recovered_attempt.number != current["attempt_count"]
                or current["revision"] != reviewer_recovered.run_revision
                or current["state"]
                != ("running" if reviewer_recovered.phase == "review_only" else "completed")
                or current["attempts"][current["attempt_count"]]["pause_requested"]
                or any(
                    item["action"] == "cancel"
                    for item in reviewer_controls.get(recovered_attempt_id, [])
                )
            ):
                raise ValueError("Reviewer recovery temporal authority changed.")
            projected = reviewer_recovery.successor(
                reviewer_recovered,
                reviewer_claim_events[recovered_attempt_id],
                ReviewerClaim.model_validate(reviewer_claims[recovered_attempt_id]),
                Producer.model_validate(reviewer_epochs[recovered_attempt_id]),
                reviewer_recoveries.get(recovered_attempt_id),
            )
            actual_run = await db.get(ResearchRun, identifier)
            receipt = await reviewer_receipt.prove(db, owner, actual_run, recovered_attempt)
            recovered_binding = await db.get(
                ResearchHarnessThread, uuid.UUID(reviewer_recovered.binding_id)
            )
            if (
                reviewer_recovered.review_id != str(receipt.id)
                or reviewer_recovered.review_hash != receipt.document_hash
                or recovered_binding is None
                or recovered_binding.run_id != identifier
                or recovered_binding.owner_id != owner
                or recovered_binding.attempt_id != recovered_attempt_id
                or recovered_binding.binding_hash != reviewer_recovered.binding_hash
            ):
                raise ValueError("Reviewer recovery historical receipt changed.")
            reviewer_epochs[recovered_attempt_id] = projected.model_dump(mode="json")
            reviewer_recoveries[recovered_attempt_id] = event
        elif kind == "reviewer.death_stopped":
            from app.research import reviewer_death_stop
            from app.research.reviewer_identity import ReviewerClaim
            from app.research.reviewer_recovery_contracts import DeathStopRecord, Producer

            death_record = DeathStopRecord.model_validate(payload)
            death_attempt_id = uuid.UUID(death_record.observation.claim.attempt_id)
            death_attempt = await db.get(ResearchAttempt, death_attempt_id)
            current = runs[identifier]
            death_controls = reviewer_controls.get(death_attempt_id, [])
            has_cancel = any(item["action"] == "cancel" for item in death_controls)
            if (
                event.entity_kind != "run"
                or event.actor_id != owner
                or current["kind"] != "reviewer"
                or death_record.observation.claim.owner_id != str(owner)
                or death_record.observation.claim.run_id != str(identifier)
                or death_attempt_id in reviewer_deaths
                or death_attempt_id in reviewer_stops
                or death_attempt_id in reviewer_reviews
                or death_attempt_id in reviewer_sdk_done
                or death_attempt is None
                or death_attempt.run_id != identifier
                or death_attempt.number != current["attempt_count"]
                or current["state"] != death_record.before_state
                or current["revision"] != death_record.run_revision
                or has_cancel != (death_record.state == "cancelled")
                or (
                    has_cancel
                    and (
                        death_controls[-1]["action"] != "cancel"
                        or death_controls[-1]["control_index"] != death_record.control_index
                    )
                )
            ):
                raise ValueError("Reviewer death stop temporal authority changed.")
            reviewer_death_stop.original(
                death_record,
                reviewer_claim_events[death_attempt_id],
                ReviewerClaim.model_validate(reviewer_claims[death_attempt_id]),
                Producer.model_validate(reviewer_epochs[death_attempt_id]),
            )
            reviewer_deaths[death_attempt_id] = event
        elif kind == "reviewer.control_requested":
            control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
            current = runs[identifier]
            action = payload["action"]
            if action != "cancel":
                require_live_session(uuid.UUID(current["session_id"]))
            previous_controls = reviewer_controls.setdefault(
                control_attempt.id if control_attempt else uuid.UUID(payload["attempt_id"]), []
            )
            planned = current["attempts"][current["attempt_count"]]
            before_pause = planned["pause_requested"]
            revision_after = current["revision"] + (
                1 if action == "pause" and current["state"] == "running" and not before_pause else 0
            )
            if (
                event.entity_kind != "run"
                or current["kind"] != "reviewer"
                or event.actor_id != owner
                or control_attempt is None
                or control_attempt.run_id != identifier
                or control_attempt.number != current["attempt_count"]
                or set(payload)
                != {
                    "attempt_id",
                    "action",
                    "control_index",
                    "key_hash",
                    "request_hash",
                    "input_hash",
                    "revision_before",
                    "revision_after",
                    "pause_requested",
                }
                or action not in {"pause", "resume", "cancel"}
                or (action != "cancel" and control_attempt.id in reviewer_reviews)
                or type(payload["control_index"]) is not int
                or payload["control_index"] != len(previous_controls) + 1
                or len(previous_controls) >= 100
                or any(
                    item["key_hash"] == payload["key_hash"] or item["action"] == "cancel"
                    for item in previous_controls
                )
                or payload["input_hash"] != current["input_hash"]
                or type(payload["revision_before"]) is not int
                or type(payload["revision_after"]) is not int
                or payload["revision_before"] != current["revision"]
                or payload["revision_after"] != revision_after
                or payload["pause_requested"] is not (action == "pause")
                or payload["request_hash"]
                != logical_hash(
                    dict(
                        run_id=str(identifier),
                        attempt_id=payload["attempt_id"],
                        revision=payload["revision_before"],
                        action=action,
                        key=payload["key_hash"],
                    )
                )
                or (action == "pause" and current["state"] not in {"running", "waiting_approval"})
                or (
                    action == "resume"
                    and (
                        not before_pause
                        or current["state"] not in {"running", "paused", "waiting_approval"}
                    )
                )
                or (
                    action == "cancel"
                    and current["state"] not in {"queued", "running", "paused", "waiting_approval"}
                )
            ):
                raise ValueError("Reviewer control request temporal proof changed.")
            previous_controls.append(payload)
            current["revision"] = revision_after
            planned["pause_requested"] = action == "pause"
        elif kind == "reviewer.control_acknowledged":
            control_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
            current = runs[identifier]
            from app.models.research_planning import ResearchPlanningLease

            control_lease = (
                await db.get(ResearchPlanningLease, control_attempt.id) if control_attempt else None
            )
            controls = reviewer_controls.get(uuid.UUID(payload["attempt_id"]), [])
            index = payload["control_index"]
            ack_fields = {
                "attempt_id",
                "control_index",
                "action",
                "revision",
                "proof",
                "fence",
                "worker_id",
            }
            if payload.get("proof") == "verified_death":
                ack_fields |= {"death_event_id", "death_hash"}
            elif payload.get("proof") == "worker_boundary":
                ack_fields |= {"boundary_event_id", "boundary_hash"}
            if (
                event.entity_kind != "run"
                or current["kind"] != "reviewer"
                or control_attempt is None
                or control_attempt.run_id != identifier
                or control_attempt.number != current["attempt_count"]
                or set(payload) != ack_fields
                or not controls
                or type(index) is not int
                or index != controls[-1]["control_index"]
                or (control_attempt.id, index) in reviewer_control_acks
                or payload["action"] != controls[-1]["action"]
                or payload["revision"] != current["revision"]
                or type(payload["revision"]) is not int
                or payload["proof"] not in {"worker_boundary", "never_claimed", "verified_death"}
                or (payload["proof"] == "worker_boundary" and control_attempt.id in reviewer_deaths)
                or event.actor_id != owner
                or (
                    payload["proof"] == "never_claimed"
                    and (
                        control_lease is not None
                        or payload["fence"] is not None
                        or payload["worker_id"] is not None
                    )
                )
                or (
                    payload["proof"] == "worker_boundary"
                    and (
                        control_lease is None
                        or type(payload["fence"]) is not int
                        or payload["fence"]
                        != reviewer_epochs.get(control_attempt.id, {}).get(
                            "fence", control_lease.fence
                        )
                        or payload["worker_id"]
                        != reviewer_epochs.get(control_attempt.id, {}).get(
                            "worker_id", control_lease.worker_id
                        )
                    )
                )
                or (
                    payload["proof"] == "never_claimed"
                    and (payload["action"] != "cancel" or current["state"] != "cancelled")
                )
                or (payload["action"] == "cancel" and current["state"] != "cancelled")
                or (
                    payload["action"] == "pause"
                    and current["state"] not in {"paused", "waiting_approval"}
                )
                or (
                    payload["action"] == "resume"
                    and current["state"] not in {"running", "waiting_approval"}
                )
            ):
                raise ValueError("Reviewer control acknowledgement changed.")
            if payload["proof"] == "verified_death":
                death_event = reviewer_deaths.get(control_attempt.id)
                death_stop = reviewer_stops.get(control_attempt.id, {})
                if (
                    death_event is None
                    or control_lease is None
                    or payload["action"] != "cancel"
                    or death_stop.get("proof") != "verified_death"
                    or payload["death_event_id"] != str(death_event.id)
                    or payload["death_hash"] != death_event.logical_hash
                    or event.sequence != death_event.sequence + 3
                    or type(payload["fence"]) is not int
                    or payload["fence"] != reviewer_epochs[control_attempt.id]["fence"]
                    or payload["worker_id"] != reviewer_epochs[control_attempt.id]["worker_id"]
                    or payload["death_event_id"] != death_stop.get("death_event_id")
                    or payload["death_hash"] != death_stop.get("death_hash")
                ):
                    raise ValueError("Reviewer death acknowledgement changed.")
            if payload["proof"] == "worker_boundary":
                self.consume_ack(event, payload, current)
            reviewer_control_acks.add((control_attempt.id, index))
        elif kind == "reviewer.stopped":
            from app.models.research_planning import ResearchPlanningLease

            attempt_id = uuid.UUID(payload["attempt_id"])
            current = runs[identifier]
            stop_attempt = await db.get(ResearchAttempt, attempt_id)
            stop_lease = await db.get(ResearchPlanningLease, attempt_id, populate_existing=True)
            stop_fields = {
                "attempt_id",
                "fence",
                "worker_id",
                "producer_host",
                "producer_pid",
                "producer_birth",
                "state",
                "proof",
                "released_at_ms",
            }
            if payload.get("proof") == "verified_death":
                stop_fields |= {"death_event_id", "death_hash"}
            elif payload.get("proof") == "owned_boundary":
                stop_fields |= {"boundary_event_id", "boundary_hash"}
            if (
                event.entity_kind != "run"
                or event.actor_id != owner
                or current["kind"] != "reviewer"
                or attempt_id in reviewer_stops
                or stop_attempt is None
                or stop_lease is None
                or stop_attempt.run_id != identifier
                or stop_attempt.number != current["attempt_count"]
                or set(payload) != stop_fields
                or payload["proof"] not in {"owned_boundary", "verified_death"}
                or type(payload["fence"]) is not int
                or type(payload["producer_pid"]) is not int
                or type(payload["released_at_ms"]) is not int
                or payload["state"] not in {"failed", "cancelled", "interrupted"}
                or stop_lease.active_owner_id is not None
                or stop_lease.owner_id != owner
                or stop_lease.released_at_ms != payload["released_at_ms"]
                or any(
                    getattr(stop_lease, name) != payload[name]
                    for name in (
                        "fence",
                        "worker_id",
                        "producer_host",
                        "producer_pid",
                        "producer_birth",
                    )
                )
            ):
                raise ValueError("Reviewer terminal release proof changed.")
            if payload["proof"] == "verified_death":
                from app.research import reviewer_lease

                death_event = reviewer_deaths.get(attempt_id)
                if death_event is None or payload["death_event_id"] != str(death_event.id):
                    raise ValueError("Reviewer stop lacks its prior death event.")
                actual_run = await db.get(ResearchRun, identifier)
                await reviewer_lease.prove_stop(db, owner, actual_run, stop_lease)
            else:
                self.consume_stop(event, payload, current)
            require_transition(State(current["state"]), State(payload["state"]))
            if payload["state"] != "cancelled":
                from app.research import reviewer_lease

                if await reviewer_lease.review_event(db, attempt_id) is not None:
                    raise ValueError("Receipted Reviewer cannot be interrupted or retried.")
            if payload["state"] == "cancelled" and not any(
                item["action"] == "cancel" for item in reviewer_controls.get(attempt_id, [])
            ):
                raise ValueError("Reviewer cancel stop lacks its request.")
            current["attempts"][current["attempt_count"]]["pause_requested"] = False
            reviewer_stops[attempt_id] = payload
            self.stop_events[attempt_id] = event
        elif kind == "reviewer.sdk_finished":
            from app.models.research_harness import ResearchHarnessThread
            from app.research import reviewer_sdk_finish
            from app.research.reviewer_identity import ReviewerClaim
            from app.research.reviewer_recovery_contracts import Producer

            current = runs[uuid.UUID(payload["run_id"])]
            sdk_attempt_id = uuid.UUID(payload["attempt_id"])
            sdk_attempt = current["attempts"][current["attempt_count"]]
            if (
                current["kind"] != "reviewer"
                or sdk_attempt_id in reviewer_sdk_done
                or sdk_attempt_id not in self.claims
                or current["state"] != "completed"
                or sdk_attempt["domain_kind"] != "reviewer_receipt"
                or sdk_attempt["domain_run_id"] != payload["review_id"]
                or sdk_attempt["completion_hash"] != payload["review_hash"]
            ):
                raise ValueError("Reviewer SDK finish precedes business completion.")
            actual_run = await db.get(ResearchRun, uuid.UUID(payload["run_id"]))
            thread = await db.get(ResearchHarnessThread, uuid.UUID(payload["binding_id"]))
            if thread is None:
                raise ValueError("Reviewer SDK thread missing.")
            proof = await reviewer_sdk_finish.verify(
                db,
                owner,
                actual_run,
                thread,
                claim_event=self.claim_events[sdk_attempt_id],
                claim=ReviewerClaim.model_validate(self.claims[sdk_attempt_id]),
                epoch=Producer.model_validate(self.epochs[sdk_attempt_id]),
                recovery=self.recoveries.get(sdk_attempt_id),
            )
            if proof.id != event.id:
                raise ValueError("Reviewer SDK event identity changed.")
            reviewer_sdk_done.add(uuid.UUID(payload["attempt_id"]))
        elif kind in {"reviewer.review_receipted", "reviewer.tool_recorded"}:
            from app.research import reviewer_receipt, reviewer_runtime

            run_id = uuid.UUID(payload["run_id"])
            current = runs[run_id]
            require_live_session(uuid.UUID(current["session_id"]))
            actual_attempt = await db.get(ResearchAttempt, uuid.UUID(payload["attempt_id"]))
            if (
                current["kind"] != "reviewer"
                or current["state"] != "running"
                or actual_attempt is None
                or actual_attempt.run_id != run_id
                or actual_attempt.number != current["attempt_count"]
                or current["attempts"][current["attempt_count"]]["pause_requested"]
                or any(
                    item["action"] == "cancel"
                    for item in reviewer_controls.get(actual_attempt.id, [])
                )
            ):
                raise ValueError("Reviewer result temporal binding changed.")
            actual_run = await db.get(ResearchRun, run_id)
            if kind == reviewer_receipt.EVENT:
                receipt = await reviewer_receipt.prove(db, owner, actual_run, actual_attempt)
                if (
                    receipt.id != event.id
                    or payload["run_revision"] != current["revision"]
                    or payload["fence"] != self.epochs[actual_attempt.id]["fence"]
                    or actual_attempt.id in reviewer_reviews
                ):
                    raise ValueError("Reviewer review event identity or producer changed.")
                reviewer_reviews.add(actual_attempt.id)
            else:
                await reviewer_runtime.prove_event(db, owner, event)
        else:
            raise ValueError("Unknown independent Reviewer event.")
