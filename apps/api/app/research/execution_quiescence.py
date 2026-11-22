"""Prove the fixed native identity died; missing dispatch evidence stays unknown."""

from app.research.execution_identity import producer_dead
from app.research.execution_intent import directory, verify_document
from app.research.migration import MigrationRefusedError
from app.research.produced import load_json, read_bytes


def stage_not_created(intent, token, input_hash: str) -> bool:
    document = verify_document(intent, token, current_input_hash=input_hash)
    try:
        directory(document)
    except FileNotFoundError:
        return True
    except (OSError, ValueError, MigrationRefusedError):
        return False
    return False


def native_quiescent(intent, dispatch, token, host: str, input_hash: str) -> bool:
    if dispatch is None:
        # A surviving Intent can contradict a deleted Dispatch and retained
        # launch/result bytes. Its absence of Dispatch never proves no work.
        return intent is None
    if (
        intent is None
        or dispatch.intent_hash != intent.intent_hash
        or dispatch.fence != intent.fence
    ):
        return False
    document = verify_document(intent, token, current_input_hash=input_hash)
    try:
        launch = load_json(read_bytes(directory(document) / "launch.json", 65536))
    except (OSError, ValueError, MigrationRefusedError):
        return False
    if (
        set(launch) != {"version", "intent_hash", "producer"}
        or type(launch["version"]) is not int
        or launch["version"] != 1
        or launch["intent_hash"] != intent.intent_hash
        or not isinstance(launch["producer"], dict)
        or set(launch["producer"]) != {"pid", "birth"}
        or type(launch["producer"]["pid"]) is not int
        or launch["producer"]["pid"] <= 0
    ):
        return False
    return producer_dead(host, launch["producer"]["pid"], launch["producer"]["birth"])
