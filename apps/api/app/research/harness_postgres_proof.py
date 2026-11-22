"""Server-authenticated references distinguish consumed channels from lost blobs."""

import hashlib
import hmac
import json

from app.config import settings
from app.services import research_service as ledger

FIELD = "statforge_h02_proof"


def require_configuration():
    configured = settings.h02_checkpoint_integrity_key
    if configured is None or len(configured.get_secret_value().encode()) < 32:
        raise ledger.error("HARNESS_POSTGRES_PROOF_CONFIG_REFUSED")


def _mac(binding, checkpoint, metadata, references, route):
    # Independent durable root. No credential or derived key is persisted.
    # Key rotation deliberately refuses old checkpoints; it never re-signs them.
    require_configuration()
    configured = settings.h02_checkpoint_integrity_key
    assert configured is not None
    secret = configured.get_secret_value().encode()
    key = hmac.digest(secret, b"statforge-h02-postgres-integrity-v2", "sha256")
    document = dict(
        binding_hash=binding.binding_hash,
        checkpoint=checkpoint,
        metadata=metadata,
        references=references,
        route=route,
    )
    encoded = json.dumps(
        document, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def attach(binding, checkpoint, metadata, references, route):
    if FIELD in metadata:
        raise ledger.error("HARNESS_POSTGRES_PROOF_REFUSED")
    return dict(
        metadata,
        **{
            FIELD: dict(
                version=2,
                references=references,
                route=route,
                mac=_mac(binding, checkpoint, metadata, references, route),
            )
        },
    )


def verify(binding, checkpoint, metadata, route):
    if not isinstance(metadata, dict):
        raise ledger.error("HARNESS_POSTGRES_PROOF_REFUSED")
    unsigned = dict(metadata)
    proof = unsigned.pop(FIELD, None)
    if (
        not isinstance(proof, dict)
        or set(proof) != {"version", "references", "mac", "route"}
        or proof["version"] != 2
        or proof["route"] != route
        or checkpoint.get("id") != route["checkpoint_id"]
        or type(proof["references"]) is not list
        or len(proof["references"]) > 4096
        or type(proof["mac"]) is not str
        or len(proof["mac"]) != 64
        or any(char not in "0123456789abcdef" for char in proof["mac"])
    ):
        raise ledger.error("HARNESS_POSTGRES_PROOF_REFUSED")
    for item in proof["references"]:
        if (
            type(item) is not dict
            or set(item) != {"channel", "version", "sha256"}
            or any(type(value) is not str for value in item.values())
            or len(item["channel"]) > 512
            or len(item["version"]) > 128
            or len(item["sha256"]) != 64
        ):
            raise ledger.error("HARNESS_POSTGRES_PROOF_REFUSED")
    if not hmac.compare_digest(
        proof["mac"], _mac(binding, checkpoint, unsigned, proof["references"], route)
    ):
        raise ledger.error("HARNESS_POSTGRES_PROOF_REFUSED")
    return proof["references"]
