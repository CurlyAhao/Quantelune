"""Hold verified bytes before short transactions; scoped metadata-only rechecks."""

import hashlib
import os
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from app.config import settings
from app.research.migration import MigrationRefusedError
from app.research.output_handles import lexical, long_path, opened

_VERIFIER: ContextVar = ContextVar("publication_byte_verifier", default=None)


def delegated(key, size, digest) -> bool:
    verifier = _VERIFIER.get()
    if verifier is None:
        return False
    return verifier(key, size, digest) is not False


@contextmanager
def verifier_context(verifier):
    token = _VERIFIER.set(verifier)
    try:
        yield
    finally:
        _VERIFIER.reset(token)


def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class HeldBytes:
    def __init__(self, stack: ExitStack, deadline: float):
        self.stack, self.deadline = stack, deadline
        self.files: dict[str, tuple[Any, ...]] = {}

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise MigrationRefusedError("Publication exceeded its bounded preparation deadline.")

    def path(self, key):
        parts = key.split("/")
        if any(not part or part in {".", ".."} or "\\" in part or ":" in part for part in parts):
            raise MigrationRefusedError("Unknown authorized source key.")
        return Path(long_path(lexical(Path(settings.local_storage_path)).joinpath(*parts)))

    def capture(self, key, size, digest):
        self.check_time()
        if key in self.files:
            self.verify(key, size, digest)
            return
        path = self.path(key)
        stream = self.stack.enter_context(opened(path))
        hashed, total = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            self.check_time()
            total += len(chunk)
            if total > size:
                raise MigrationRefusedError("Authorized source byte size changed.")
            hashed.update(chunk)
        if total != size or hashed.hexdigest() != digest:
            raise MigrationRefusedError("Authorized source byte digest changed.")
        descriptor_identity, path_identity = (
            identity(os.fstat(stream.fileno())),
            identity(path.lstat()),
        )
        if descriptor_identity[:4] != path_identity[:4]:
            raise MigrationRefusedError("Held path and descriptor identify different files.")
        self.files[key] = (path, stream, size, digest, descriptor_identity, path_identity)
        self.verify(key, size, digest)

    def verify(self, key, size, digest):
        self.check_time()
        record = self.files.get(key)
        if record is None:
            raise MigrationRefusedError("New file appeared after publication verification.")
        path, stream, expected_size, expected_digest, captured, captured_path = record
        if (
            (size, digest) != (expected_size, expected_digest)
            or identity(os.fstat(stream.fileno())) != captured
            or identity(path.lstat()) != captured_path
        ):
            raise MigrationRefusedError("Held source identity or metadata changed.")

    @contextmanager
    def context(self, *, collect=False):
        token = _VERIFIER.set(self.capture if collect else self.verify)
        try:
            yield
        finally:
            _VERIFIER.reset(token)
