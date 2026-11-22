"""Fresh actual business/Artifact proof for execute; legacy adopt is unchanged."""

import hashlib
import uuid
from dataclasses import replace

from app.models.research_execution import (
    ResearchExecutionIntent,
    ResearchExecutionReceipt,
    ResearchPublicationIntent,
    ResearchPublicationReceipt,
)
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.domain_results import SavedOutput, load_snapshot
from app.research.produced import load_json
from app.services import research_service as ledger


async def binding(db, owner, run, attempt):
    intent = await db.get(ResearchExecutionIntent, attempt.id, populate_existing=True)
    produced = await db.get(ResearchExecutionReceipt, attempt.id, populate_existing=True)
    publication = await db.get(ResearchPublicationIntent, attempt.id, populate_existing=True)
    if intent is None or produced is None or publication is None:
        raise ledger.error("SOURCE_UNPROVEN")
    document, plan = parse_document(intent.document_json), parse_document(publication.document_json)
    payload = publication.result_json.encode()
    manifest = parse_document(produced.document_json)
    if (
        logical_hash(document) != intent.intent_hash
        or logical_hash(plan) != publication.plan_hash
        or document["owner_id"] != str(owner)
        or document["run_id"] != str(run.id)
        or document["attempt_id"] != str(attempt.id)
        or document["input_hash"] != intent.input_hash
        or intent.input_hash != run.input_hash
        or document["domain_id"] != str(intent.domain_id)
        or document["fence"] != intent.fence
        or document["staging_id"] != intent.staging_id
        or plan["owner_id"] != str(owner)
        or plan["attempt_id"] != str(attempt.id)
        or plan["run_id"] != str(run.id)
        or plan["intent_hash"] != intent.intent_hash
        or plan["input_hash"] != run.input_hash
        or plan["domain_id"] != str(intent.domain_id)
        or plan["domain_kind"] != run.kind
        or plan["root_id"] != str(publication.root_id)
        or plan["manifest_sha256"] != publication.manifest_sha256
        or produced.manifest_sha256 != publication.manifest_sha256
        or attempt.run_id != run.id
        or manifest.get("version") != 2
        or manifest["intent_hash"] != intent.intent_hash
        or manifest["operation"] != document["operation"]
        or manifest["dataset_sha256"] != document["source"]["sha256"]
        or manifest["config_hash"] != logical_hash(document["configuration"])
        or hashlib.sha256(canonical_bytes(manifest)).hexdigest() != produced.manifest_sha256
        or not 0 < len(payload) <= 16 * 1024 * 1024
        or len(payload) != produced.result_bytes
        or hashlib.sha256(payload).hexdigest() != produced.result_sha256
        or manifest["result"]["sha256"] != produced.result_sha256
        or manifest["result"]["bytes"] != produced.result_bytes
    ):
        raise ledger.error("SOURCE_UNPROVEN")
    response = load_json(payload)
    from app.research.publication_files import targets

    if plan["files"] != targets(document, response, manifest):
        raise ledger.error("SOURCE_UNPROVEN")
    return intent, produced, publication, document, plan, response


async def snapshot(db, owner, run, attempt):
    intent, produced, _publication, document, plan, response = await binding(
        db, owner, run, attempt
    )
    if run.domain_kind != document["domain_kind"] or run.domain_run_id != intent.domain_id:
        raise ledger.error("SOURCE_UNPROVEN")
    base = await load_snapshot(db, owner, run.domain_kind, run.domain_run_id)
    all_outputs = {output.object_key: output for output in base.outputs}
    if run.kind == "workflow":
        for record in response["result"]["model_records"]:
            model = await load_snapshot(db, owner, "ml", uuid.UUID(record["id"]))
            actual = model.document["run"]
            expected_run = {
                "status": record["result"]["status"],
                "random_seed": record["config"]["random_seed"],
                "validation_strategy": record["config"]["split_strategy"],
                "metrics": record["result"].get("models", []),
                "split": record["result"].get("split", {}),
                "comparison": record["result"].get("comparison", {}),
                "leakage_checks": record["result"].get("leakage_checks", []),
                "n_observations": record["result"].get("n_observations"),
                "artifact_meta": record["artifact_meta"],
                "software_versions": record["result"].get("software_versions", {}),
                "error_code": record["result"].get("error_code"),
                "error_message": record["result"].get("error_message"),
            }
            from app.services.ml_service import config_from_dict

            configured = config_from_dict(record["config"])
            experiment = model.document["experiment"]
            if (
                model.state.value != "completed"
                or actual != expected_run
                or experiment["config"] != record["config"]
                or experiment["task_type"] != configured.task_type.value
                or experiment["target_variable"] != configured.target
                or experiment["feature_variables"] != list(configured.features)
                or experiment["categorical_variables"] != list(configured.categorical_features)
                or experiment["dataset_sha256"] != record["dataset_sha256"]
                or model.datasets[0]["id"] != record["dataset_id"]
                or model.datasets[0]["sha256"] != record["dataset_sha256"]
                or len(model.outputs) != 1
                or model.outputs[0].object_key != record["artifact_key"]
            ):
                raise ledger.error("SOURCE_UNPROVEN")
    for item in plan["files"]:
        ledger.verify_local_bytes(item["object_key"], item["bytes"], item["sha256"])
        output = SavedOutput(
            item["object_key"], item["kind"], item["media_type"], item["bytes"], item["sha256"]
        )
        previous = all_outputs.get(output.object_key)
        if previous is not None and previous != output:
            raise ledger.error("SOURCE_UNPROVEN")
        all_outputs[output.object_key] = output
    outputs = tuple(all_outputs[key] for key in sorted(all_outputs))
    # Domain scientific semantics stay identical for execute and legacy adopt.
    # Immutable plan/producer receipt separately binds the extra physical bytes.
    return replace(base, outputs=outputs)


async def prove(db, owner, run, attempt, current_snapshot):
    intent, produced, publication, document, plan, _response = await binding(
        db, owner, run, attempt
    )
    receipt = await db.get(ResearchPublicationReceipt, attempt.id, populate_existing=True)
    if (
        receipt is None
        or receipt.plan_hash != publication.plan_hash
        or receipt.manifest_sha256 != produced.manifest_sha256
        or receipt.snapshot_hash != current_snapshot.logical_hash
        or receipt.root_id != publication.root_id
        or receipt.publication_fence < intent.fence
        or receipt.state != current_snapshot.state.value
        or document["domain_id"] != str(run.domain_run_id)
        or parse_document(run.input_json)["parameters"].get("operation") != "execute"
    ):
        raise ledger.error("SOURCE_UNPROVEN")
