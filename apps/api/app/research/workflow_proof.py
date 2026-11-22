"""Close node, preallocated identity, derived-source and actual model references."""

import uuid

from app.research.migration import MigrationRefusedError
from app.research.produced import load_json, read_bytes


def verify_links(directory, result, context, objects):
    def require(condition):
        if not condition:
            raise MigrationRefusedError("Workflow frozen node/object binding changed.")

    nodes = {node["id"]: node for node in context["graph"]["nodes"]}
    results = result["node_results"]
    require(set(results) == set(nodes) and set(result["order"]) == set(nodes))
    require(len(result["order"]) == len(nodes))
    files = {item["name"]: item for item in objects}
    shas = {identity: item["sha256"] for identity, item in context["sources"].items()}
    for item in context["sources"].values():
        shas.update(
            {ancestor["dataset_id"]: ancestor["dataset_sha256"] for ancestor in item["ancestors"]}
        )
    derived = result["dataset_records"]
    require(isinstance(derived, list) and len(derived) <= len(nodes))
    derived_ids = set()
    for record in derived:
        identity = record["id"]
        require(
            identity not in derived_ids
            and identity in {ids["dataset"] for ids in context["identities"].values()}
        )
        derived_ids.add(identity)
        require(record["project_id"] == context["project_id"])
        metadata = load_json(read_bytes(directory / record["metadata_name"], 16 * 1024 * 1024))
        require(
            metadata["dataset_id"] == identity and metadata["dataset_sha256"] == record["sha256"]
        )
        require(metadata["workflow_id"] == context["workflow_id"])
        require(metadata["workflow_run_id"] == context["workflow_run_id"])
        require(metadata["workflow_version"] == context["workflow_version"])
        require(shas.get(metadata["parent_dataset_id"]) == metadata["parent_sha256"])
        require(
            all(
                shas.get(source["dataset_id"]) == source["dataset_sha256"]
                for source in metadata["ancestors"]
            )
        )
        metadata_sha = files[record["metadata_name"]]["sha256"]
        prefix = f"workflow-derived-data/{context['project_id']}/{identity}/{metadata_sha}"
        require(record["object_key"] == prefix + "/input.parquet")
        require(record["metadata_key"] == prefix + "/provenance.json")
        shas[identity] = record["sha256"]
    models = result["model_records"]
    require(isinstance(models, list) and len(models) <= len(nodes))
    completed_models = {
        nid
        for nid, node in nodes.items()
        if node["type"] in {"model", "cross_validation"} and results[nid]["status"] == "completed"
    }
    require({record["node_id"] for record in models} == completed_models)
    require(len(models) == len(completed_models))
    for record in models:
        nid = record["node_id"]
        ids = context["identities"][nid]
        output = results[nid]["output"]
        source = output["source"]
        require(record["id"] == ids["ml_run"] and record["experiment_id"] == ids["ml_experiment"])
        require(
            source["ml_run_id"] == record["id"]
            and source["ml_experiment_id"] == record["experiment_id"]
        )
        require(source["dataset_id"] == record["dataset_id"])
        require(
            source["dataset_sha256"] == record["dataset_sha256"] == shas.get(record["dataset_id"])
        )
        require(
            source["workflow_id"] == context["workflow_id"]
            and source["workflow_run_id"] == context["workflow_run_id"]
        )
        require(
            source["workflow_version"] == context["workflow_version"] and source["node_id"] == nid
        )
        require(record["config"] == output["input_config"])
        require(record["result"]["status"] == "completed")
        for key in ("models", "split", "comparison", "leakage_checks", "software_versions"):
            require(
                record["result"].get(key, {} if key not in {"models", "leakage_checks"} else [])
                == output[key]
            )
        require(record["staged_name"] == f"wf-{uuid.UUID(record['id']).hex}-model.joblib")
        model_prefix = f"ml-artifacts/{context['project_id']}/{record['experiment_id']}"
        require(record["artifact_key"] == model_prefix + "/" + record["id"] + ".joblib")
        require(record["artifact_meta"] == source["artifact"])
        require(
            record["artifact_meta"]["evaluation_protocol_id"]
            == output["split"]["evaluation_protocol"]["protocol_id"]
        )
        require(
            record["artifact_meta"]["models"]
            == sorted(
                model["model_id"] for model in output["models"] if model["status"] == "completed"
            )
        )
    require(
        all(
            shas.get(source["dataset_id"]) == source["dataset_sha256"]
            for source in result["sources"]
        )
    )
    require({source["dataset_id"] for source in result["sources"]} == set(shas))
    for nid, node in results.items():
        require(node["node_type"] == nodes[nid]["type"])
        if node["status"] != "completed":
            continue
        for reference in (node.get("output") or {}).get("artifacts", []):
            identity = uuid.UUID(context["identities"][nid]["artifact"]).hex
            run_prefix = f"workflow-artifacts/{context['project_id']}/{context['workflow_run_id']}"
            prefix = run_prefix + "/" + identity
            require(reference["id"] == identity)
            require(reference["object_key"] == prefix + "/" + reference["filename"])
            require(
                "/" not in reference["filename"]
                and "\\" not in reference["filename"]
                and ".." not in reference["filename"]
            )
            require(reference["staged_name"] == f"wf-{identity}-output.bin")
