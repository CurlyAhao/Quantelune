"""Revocable read capability over an already excluded, single backend snapshot."""

from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.research.harness_saver import BoundSaver
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger


class ReadOnlySaver(BaseCheckpointSaver):
    """No live owner DB transaction or delegate forwarding during a fixed read."""

    def _config(self, config):
        # These existing pure checks require only binding and guard_serde;
        # adaptation does not inherit the writable BoundSaver capability.
        BoundSaver._config(cast(BoundSaver, self), config)

    def _tuple(self, result):
        BoundSaver._tuple(cast(BoundSaver, self), result)

    def __init__(self, delegate, binding, guard):
        if type(delegate.serde) is not SafeSerializer:
            raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
        super().__init__(serde=delegate.serde)
        self._reader = delegate
        self.binding = binding
        self.guard_serde = delegate.serde
        self._raw_guard = guard
        self._closed = False
        self._fixed_id = None

    def close(self):
        self._closed = True

    async def _guard(self, config):
        if self._closed:
            raise ledger.error("HARNESS_SNAPSHOT_CLOSED")
        self._config(config)
        values = config["configurable"]
        if values.get("checkpoint_ns", "") or values.get("checkpoint_map"):
            raise ledger.error("HARNESS_NAMESPACE_REFUSED")
        if self._fixed_id is None and "checkpoint_id" in values:
            raise ledger.error("HARNESS_SNAPSHOT_CHANGED")
        if self._fixed_id is not None and values.get("checkpoint_id") != self._fixed_id:
            raise ledger.error("HARNESS_SNAPSHOT_CHANGED")
        await self._raw_guard(config)

    async def aget_tuple(self, config):
        await self._guard(config)
        result = await self._reader.aget_tuple(config)
        if result is None:
            raise ledger.error("HARNESS_CHECKPOINT_UNPROVEN")
        self._tuple(result)
        identifier = result.config.get("configurable", {}).get("checkpoint_id")
        if (
            not isinstance(identifier, str)
            or not 1 <= len(identifier) <= 128
            or result.checkpoint.get("id") != identifier
            or self._fixed_id is not None
            and identifier != self._fixed_id
            or config["configurable"].get("checkpoint_id", identifier) != identifier
        ):
            raise ledger.error("HARNESS_SNAPSHOT_CHANGED")
        self._fixed_id = identifier
        await self._raw_guard(result.config)
        return result

    async def aget_delta_channel_history(self, *, config, channels):
        await self._guard(config)
        if (
            self._fixed_id is None
            or type(channels) not in {list, tuple}
            or len(channels) > 2
            or any(type(channel) is not str for channel in channels)
            or len(set(channels)) != len(channels)
            or set(channels) - {"messages", "files"}
        ):
            raise ledger.error("HARNESS_DELTA_CHANNEL_REFUSED")
        result = await self._reader.aget_delta_channel_history(config=config, channels=channels)
        if type(result) is not dict or set(result) - set(channels):
            raise ledger.error("HARNESS_DELTA_CHANNEL_REFUSED")
        for channel, history in result.items():
            if (
                type(history) is not dict
                or set(history) - {"seed", "writes"}
                or type(history.get("writes")) is not list
                or len(history["writes"]) > 20_000
            ):
                raise ledger.error("HARNESS_DELTA_CHANNEL_REFUSED")
            for write in history["writes"]:
                if (
                    type(write) is not tuple
                    or len(write) != 3
                    or type(write[0]) is not str
                    or len(write[0]) > 128
                    or write[1] != channel
                ):
                    raise ledger.error("HARNESS_DELTA_CHANNEL_REFUSED")
        self.guard_serde.check(result)
        await self._raw_guard(config)
        return result

    def _refuse(self, *args, **kwargs):
        raise ledger.error("HARNESS_SNAPSHOT_READ_ONLY")

    async def _arefuse(self, *args, **kwargs):
        self._refuse()

    # Explicitly cover installed public mutation surfaces, including batch APIs.
    put = put_writes = delete_thread = delete_for_runs = copy_thread = prune = _refuse
    aput = aput_writes = adelete_thread = adelete_for_runs = acopy_thread = aprune = _arefuse
    get_next_version = _refuse
    get = get_tuple = list = get_delta_channel_history = _refuse

    async def alist(self, *args, **kwargs):
        self._refuse()
        yield  # Preserve the public async iterator shape while refusing enumeration.
