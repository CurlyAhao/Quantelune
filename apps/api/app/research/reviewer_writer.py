"""Forward-only SDK writer provenance; a stamp alone is never ledger authority."""

from collections import ChainMap
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.research import reviewer_sdk_tasks
from app.research.contracts import logical_hash
from app.research.reviewer_contracts import HashText, UuidText
from app.research.reviewer_sdk_events import Entry
from app.services import research_service as ledger

FIELD = "statforge_reviewer_writer_v1"


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    checkpoint_ns: str = Field(max_length=512)
    checkpoint_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    parent_checkpoint_id: str | None = Field(max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class Stamp(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: StrictInt
    entry_event_id: UuidText
    entry_hash: HashText
    entry: Entry
    route: Route

    @model_validator(mode="after")
    def original(self):
        if self.version != 1 or self.entry_hash != logical_hash(self.entry.model_dump(mode="json")):
            raise ValueError("Writer stamp has no exact original entry.")
        return self


def refuse_supplied(config, metadata=None):
    """Check before SDK projection can discard a caller's reserved field."""
    for value in (metadata, config.get("metadata"), config.get("configurable")):
        if value is not None and (
            not isinstance(value, Mapping)
            or any(type(key) is not str or key.startswith(FIELD) for key in value)
        ):
            raise ledger.error("REVIEWER_WRITER_FIELD_REFUSED")


def freeze(serde, config, checkpoint, metadata, versions):
    refuse_supplied(config, metadata)
    values = config["configurable"]
    # Only these public persisted coordinates affect official saver I/O. SDK
    # callbacks/closures/runtime config must not become serialized provenance.
    route = {
        key: values[key]
        for key in ("thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint_map")
        if key in values
    }
    route.setdefault("checkpoint_ns", "")
    selected = config.get("metadata")
    copied = {"configurable": route}
    if selected is not None:
        copied["metadata"] = dict(selected) if type(selected) is ChainMap else selected
    material = (
        copied,
        checkpoint,
        dict(metadata) if type(metadata) is ChainMap else metadata,
        versions,
    )
    return serde.loads_typed(serde.dumps_typed(material))


def capture(token, binding, config, checkpoint):
    event, entry = reviewer_sdk_tasks.writer_origin(token, binding.binding_hash)
    values = config["configurable"]
    if (
        entry.binding_id != str(binding.id)
        or entry.thread_id != binding.thread_id
        or entry.input_hash != binding.input_hash
        or entry.owner_id != str(binding.owner_id)
        or entry.run_id != str(binding.run_id)
        or entry.attempt_id != str(binding.attempt_id)
        or values.get("thread_id") != entry.thread_id
    ):
        raise ledger.error("REVIEWER_WRITER_UNPROVEN")
    return Stamp(
        version=1,
        entry_event_id=str(event.id),
        entry_hash=event.logical_hash,
        entry=entry,
        route=Route(
            checkpoint_ns=values.get("checkpoint_ns", ""),
            checkpoint_id=checkpoint["id"],
            parent_checkpoint_id=values.get("checkpoint_id"),
        ),
    )


def live(token, binding, config, checkpoint, metadata, stamp):
    if type(stamp) is not Stamp or capture(token, binding, config, checkpoint) != stamp:
        raise ledger.error("REVIEWER_WRITER_UNPROVEN")
    if metadata.get(FIELD) != stamp.model_dump(mode="json"):
        raise ledger.error("REVIEWER_WRITER_UNPROVEN")


def from_tuple(checkpoint, binding):
    """Structural read only; historical consumers must still prove actual entry."""
    try:
        stamp = Stamp.model_validate(checkpoint.metadata[FIELD])
        values = checkpoint.config["configurable"]
        parent = checkpoint.parent_config
        expected = Route(
            checkpoint_ns=values.get("checkpoint_ns", ""),
            checkpoint_id=values["checkpoint_id"],
            parent_checkpoint_id=parent["configurable"]["checkpoint_id"] if parent else None,
        )
        if (
            stamp.route != expected
            or checkpoint.checkpoint.get("id") != expected.checkpoint_id
            or stamp.entry.binding_id != str(binding.id)
            or stamp.entry.binding_hash != binding.binding_hash
            or stamp.entry.thread_id != values.get("thread_id")
            or stamp.entry.thread_id != binding.thread_id
            or stamp.entry.input_hash != binding.input_hash
            or stamp.entry.owner_id != str(binding.owner_id)
            or stamp.entry.run_id != str(binding.run_id)
            or stamp.entry.attempt_id != str(binding.attempt_id)
        ):
            raise ValueError("Checkpoint route or writer binding changed.")
        return stamp
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ledger.error("REVIEWER_CHECKPOINT_WRITER_UNPROVEN") from None
