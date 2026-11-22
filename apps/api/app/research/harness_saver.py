"""Owner-bound public BaseCheckpointSaver facade over the official implementations."""

import asyncio
import json
from collections import ChainMap, deque

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.research.harness_binding import authorize
from app.research.harness_serde import SafeSerializer
from app.services import research_service as ledger


class BoundSaver(BaseCheckpointSaver):
    def __init__(self, delegate, factory, binding, token, raw_guard):
        super().__init__(serde=delegate.serde)
        self.delegate, self.factory, self.binding, self.token = delegate, factory, binding, token
        self.raw_guard = raw_guard
        if type(delegate.serde) is not SafeSerializer:
            raise ledger.error("HARNESS_SERIALIZATION_REFUSED")
        self.guard_serde: SafeSerializer = delegate.serde
        self.write_lock = asyncio.Lock()

    def _config(self, config):
        if config is None or not isinstance(config, dict):
            raise ledger.error("HARNESS_CONFIG_REFUSED")
        values = config.get("configurable", {})
        if any(
            key not in {"thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint_map"}
            and not key.startswith("__pregel_")
            for key in values
        ):
            raise ledger.error("HARNESS_CONFIG_REFUSED")
        if values.get("thread_id") != self.binding.thread_id:
            raise ledger.error("HARNESS_THREAD_REFUSED", 404)
        namespace = values.get("checkpoint_ns", "")
        if (
            not isinstance(namespace, str)
            or len(namespace) > 512
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:|-="
                for char in namespace
            )
        ):
            raise ledger.error("HARNESS_NAMESPACE_REFUSED")
        if namespace:
            allowed = {"durable_business", "model", "tools", "agent", "__start__", "domain_task"}
            for segment in namespace.strip("|").split("|"):
                name, separator, identity = segment.partition(":")
                if name not in allowed or not separator or len(identity) != 36:
                    raise ledger.error("HARNESS_NAMESPACE_REFUSED")
        for key in ("checkpoint_id",):
            if key in values and (not isinstance(values[key], str) or len(values[key]) > 128):
                raise ledger.error("HARNESS_CONFIG_REFUSED")
        # Framework runtime/callback/closure values are never serialized. Only
        # the fields the public saver persists get this explicit projection.
        self.guard_serde.check(
            {
                key: values[key]
                for key in ("thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint_map")
                if key in values
            }
        )
        metadata = config.get("metadata")
        self.guard_serde.check(dict(metadata) if type(metadata) is ChainMap else metadata)

    async def _guard(self, config):
        self._config(config)
        from app.research.reader_contracts import ReaderToken
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) in {ReaderToken, ReviewerToken}:
            await authorize(self.factory, self.binding, self.token, checkpoint=True)
        else:
            await authorize(self.factory, self.binding, self.token)
        await self.raw_guard(config)

    def get_next_version(self, current, channel):
        return self.delegate.get_next_version(current, channel)

    async def aget_tuple(self, config):
        await self._guard(config)
        result = await self.delegate.aget_tuple(config)
        if result is not None:
            self._tuple(result)
        return result

    def _tuple(self, result):
        self._config(result.config)
        if result.parent_config is not None:
            self._config(result.parent_config)
        self.guard_serde.check(result.checkpoint)
        self.guard_serde.check(result.metadata)
        self.guard_serde.check(result.pending_writes)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ledger.error("HARNESS_HISTORY_REFUSED")
        await self._guard(config)
        if before is not None:
            self._config(before)
        self.guard_serde.check(filter)
        async for result in self.delegate.alist(config, filter=filter, before=before, limit=limit):
            await authorize(self.factory, self.binding, self.token)
            self._tuple(result)
            yield result

    async def aput(self, config, checkpoint, metadata, new_versions):
        from app.research import reviewer_sdk_tasks, reviewer_writer
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            self._config(config)
            config, checkpoint, metadata, new_versions = reviewer_writer.freeze(
                self.guard_serde, config, checkpoint, metadata, new_versions
            )
            async with reviewer_sdk_tasks.callback(self.token, self.binding.binding_hash):
                stamp = reviewer_writer.capture(self.token, self.binding, config, checkpoint)
                metadata = dict(self._metadata(config, metadata))
                metadata[reviewer_writer.FIELD] = stamp.model_dump(mode="json")
                return await self._aput(config, checkpoint, metadata, new_versions, stamp=stamp)
        return await self._aput(config, checkpoint, metadata, new_versions)

    def _metadata(self, config, metadata):
        from langgraph.checkpoint.base import get_checkpoint_metadata

        return get_checkpoint_metadata(config, metadata)

    def _writer(self, config, checkpoint, metadata, stamp):
        from app.research import reviewer_writer
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            reviewer_writer.live(self.token, self.binding, config, checkpoint, metadata, stamp)

    async def _aput(self, config, checkpoint, metadata, new_versions, *, stamp=None):
        async with self.write_lock:
            self._writer(config, checkpoint, metadata, stamp)
            await self._guard(config)
            if type(metadata) is ChainMap:
                metadata = dict(metadata)
            self.guard_serde.check(checkpoint)
            self.guard_serde.check(metadata)
            self.guard_serde.check(new_versions)
            projected = self._metadata(config, metadata)
            if stamp is not None and projected != metadata:
                raise ledger.error("REVIEWER_WRITER_PROJECTION_CHANGED")
            self.guard_serde.check(projected)
            size = len(self.guard_serde.dumps_typed(checkpoint)[1]) + len(
                json.dumps(projected, ensure_ascii=False).encode()
            )
            await self.raw_guard(
                config, {"kind": "checkpoint", "id": checkpoint["id"], "bytes": size}
            )
            self._writer(config, checkpoint, metadata, stamp)
            result = await self.delegate.aput(config, checkpoint, metadata, new_versions)
            self._config(result)
            return result

    async def aput_writes(self, config, writes, task_id, task_path=""):
        from app.research import reviewer_sdk_tasks, reviewer_writer
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            self._config(config)
            reviewer_writer.refuse_supplied(config)
            if type(writes) not in {list, tuple, deque} or len(writes) > 20_000:
                raise ledger.error("HARNESS_WRITES_REFUSED")
            config, _, _, frozen = reviewer_writer.freeze(
                self.guard_serde, config, {}, {}, (list(writes), task_id, task_path)
            )
            writes, task_id, task_path = frozen
            async with reviewer_sdk_tasks.callback(self.token, self.binding.binding_hash):
                return await self._aput_writes(config, writes, task_id, task_path)
        return await self._aput_writes(config, writes, task_id, task_path)

    def _writes_live(self):
        from app.research import reviewer_sdk_tasks
        from app.research.reviewer_contracts import ReviewerToken

        if type(self.token) is ReviewerToken:
            reviewer_sdk_tasks.writer_origin(self.token, self.binding.binding_hash)

    async def _aput_writes(self, config, writes, task_id, task_path=""):
        async with self.write_lock:
            self._writes_live()
            await self._guard(config)
            if type(writes) not in {list, tuple, deque} or len(writes) > 20_000:
                raise ledger.error("HARNESS_WRITES_REFUSED")
            writes = list(writes)  # SDK buffer container stays runtime-only.
            self.guard_serde.check((task_id, task_path, writes))
            if (
                not isinstance(task_id, str)
                or len(task_id) > 128
                or not isinstance(task_path, str)
                or len(task_path) > 1024
            ):
                raise ledger.error("HARNESS_WRITES_REFUSED")
            from langgraph.checkpoint.base import WRITES_IDX_MAP

            plan = {
                "kind": "writes",
                "task_id": task_id,
                "replace": all(channel in WRITES_IDX_MAP for channel, _value in writes),
                "rows": [
                    (
                        WRITES_IDX_MAP.get(channel, index),
                        len(self.guard_serde.dumps_typed(value)[1]),
                    )
                    for index, (channel, value) in enumerate(writes)
                ],
            }
            await self.raw_guard(config, plan)
            self._writes_live()
            return await self.delegate.aput_writes(config, writes, task_id, task_path)

    async def aget_delta_channel_history(self, *, config, channels):
        await self._guard(config)
        if (
            type(channels) not in {list, tuple}
            or len(channels) > 2
            or any(type(channel) is not str for channel in channels)
            or len(set(channels)) != len(channels)
            or set(channels) - {"messages", "files"}
        ):
            raise ledger.error("HARNESS_DELTA_CHANNEL_REFUSED")
        self.guard_serde.check(channels)
        result = await self.delegate.aget_delta_channel_history(config=config, channels=channels)
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
        await authorize(self.factory, self.binding, self.token)
        return result

    async def adelete_thread(self, thread_id):
        raise ledger.error("HARNESS_DELETE_REFUSED")
