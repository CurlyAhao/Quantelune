"""Pure completion observation/transition/release consumer closure."""

import uuid
from typing import Any

from app.research.contracts import parse_document


class Timeline:
    def __init__(self):
        self.observations: dict[uuid.UUID, tuple] = {}
        self.transitions: dict[uuid.UUID, Any] = {}
        self.releases: dict[uuid.UUID, tuple] = {}

    def observe(self, event, record, epoch, current, open_invocations):
        key = uuid.UUID(record.attempt_id)
        covered = {
            r.invocation_id for r in record.sdk_terminal_refs if r.terminal_kind == "recovered"
        }
        if (
            key in self.observations
            or record.producer.model_dump(mode="json") != epoch
            or record.before_state != current["state"]
            or record.before_revision != current["revision"]
            or covered != set(open_invocations)
        ):
            raise ValueError("Completion observation epoch, state or open SDK set changed.")
        self.observations[key] = (event, record)

    def transition(self, event, attempt_id, payload):
        previous = self.observations.get(attempt_id)
        if previous is None or attempt_id in self.transitions or event is None:
            raise ValueError("Completion transition has no unique observed boundary.")
        observed_event, observed = previous
        if (
            event.entity_id != uuid.UUID(observed.run_id)
            or event.sequence != observed_event.sequence + 1
            or payload["before"] != observed.before_state
            or payload["state"] != "completed"
            or payload["revision"] != observed.before_revision + 1
            or payload.get("domain_kind") != "reviewer_receipt"
            or payload.get("domain_run_id") != observed.review_id
            or payload.get("completion_hash") != observed.review_hash
        ):
            raise ValueError("Completion transition changed its observation.")
        self.transitions[attempt_id] = event

    def release(self, event, record, epoch, current):
        key = uuid.UUID(record.attempt_id)
        previous, transition = self.observations.get(key), self.transitions.get(key)
        if previous is None or transition is None or key in self.releases:
            raise ValueError("Completion release has no unique earlier transition.")
        observed_event, observed = previous
        if (
            event.entity_id != uuid.UUID(observed.run_id)
            or event.sequence != transition.sequence + 1
            or record.observation_event_id != str(observed_event.id)
            or record.observation_hash != observed_event.logical_hash
            or record.transition_event_id != str(transition.id)
            or record.transition_hash != transition.logical_hash
            or record.producer != observed.producer
            or (record.owner_id, record.run_id, record.attempt_id, record.input_hash)
            != (observed.owner_id, observed.run_id, observed.attempt_id, observed.input_hash)
            or record.producer.model_dump(mode="json") != epoch
            or record.released_at_ms < observed.observed_at_ms
            or record.review_id != observed.review_id
            or record.review_hash != observed.review_hash
            or record.completed_revision != observed.before_revision + 1
            or (current["state"], current["revision"]) != ("completed", record.completed_revision)
            or parse_document(transition.payload_json)["completion_hash"] != record.review_hash
        ):
            raise ValueError("Completion release changed its observed transition/epoch.")
        self.releases[key] = (event, record)

    def finalize(self):
        if set(self.observations) != set(self.transitions) or set(self.observations) != set(
            self.releases
        ):
            raise ValueError("Completion observation, transition and release sets differ.")

    def covered(self, attempt_id):
        previous = self.observations.get(attempt_id)
        return (
            {
                r.invocation_id
                for r in previous[1].sdk_terminal_refs
                if r.terminal_kind == "recovered"
            }
            if previous
            else set()
        )

    def final(self, run, attempt, lease):
        previous = self.releases.get(attempt.id)
        if attempt.state == "completed":
            if previous is None or lease.released_at_ms != previous[1].released_at_ms:
                raise ValueError("Completed Reviewer lacks its independently proved release.")
            record = previous[1]
            if (
                str(run.id) != record.run_id
                or run.completion_hash != record.review_hash
                or attempt.completion_hash != record.review_hash
                or str(run.domain_run_id) != record.review_id
                or str(attempt.domain_run_id) != record.review_id
            ):
                raise ValueError("Completed Reviewer final domain changed.")
        elif previous is not None:
            raise ValueError("Completion release has no completed actual Attempt.")
