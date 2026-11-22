"""Execution-local byte proof for the one explicitly owned heartbeat task."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.research.execution_lease import ExecutionToken
from app.research.migration import MigrationRefusedError
from app.research.publication_handles import HeldBytes, verifier_context


@dataclass
class _Proof:
    token: ExecutionToken
    heartbeat: asyncio.Task[Any] | None = None
    publisher: HeldBytes | None = None

    def verify(self, key: str, size: int, digest: str) -> bool:
        if asyncio.current_task() is not self.heartbeat:
            raise MigrationRefusedError("Held heartbeat proof has a different task owner.")
        # Publication may have ended while the heartbeat awaited its DB lock.
        # No live holder means ordinary byte verification, never cached permission.
        held = self.publisher
        if held is None:
            return False
        held.verify(key, size, digest)
        return True


_CURRENT: ContextVar[_Proof | None] = ContextVar("research_execution_byte_proof", default=None)


@contextmanager
def execution_scope(token: ExecutionToken):
    marker = _CURRENT.set(_Proof(token))
    try:
        yield
    finally:
        _CURRENT.reset(marker)


def register_heartbeat(token: ExecutionToken, task: asyncio.Task[Any]) -> None:
    proof = _CURRENT.get()
    if proof is None or proof.token != token or proof.heartbeat is not None:
        raise MigrationRefusedError("Heartbeat proof registration differs from its execution.")
    proof.heartbeat = task


@contextmanager
def publisher_scope(token: ExecutionToken, held: HeldBytes):
    proof = _CURRENT.get()
    if proof is None:
        yield  # Standalone publication has no other task that may borrow bytes.
        return
    if proof.token != token or proof.publisher is not None:
        raise MigrationRefusedError("Publication proof differs from its execution.")
    proof.publisher = held
    try:
        yield
    finally:
        proof.publisher = None  # Revoke before the caller closes actual file handles.


@contextmanager
def heartbeat_scope(token: ExecutionToken):
    proof = _CURRENT.get()
    if proof is None:
        yield
        return
    if proof.token != token or asyncio.current_task() is not proof.heartbeat:
        raise MigrationRefusedError("Heartbeat proof differs from its token or task owner.")
    with verifier_context(proof.verify):
        yield
