"""Version-one historical field projection from proved saved results, never a fit."""

import re
import uuid

from app.research import reader_contracts, reader_saved_descriptive, reader_sources, result_contexts
from app.research.contracts import canonical_bytes
from app.research.egress import failure
from app.research.harness_serde import SafeSerializer

# A historical receipt needs its original renderer contract. These version-one
# templates match the existing server explanation; future wording is a new
# contract version, not a reinterpretation of an already saved answer.
TEXT_V1 = {
    "status": "业务状态以保存的 Run 为准；会话结束不能代替计算完成。",
    "n": "有效样本数来自本次计算；缺失处理和筛选会改变样本量。",
    "statistic": "统计量来自保存的计算记录，需结合检验假设解释。",
    "p_value": "p 值衡量指定零假设下数据的不相容程度；不能证明效应大小或因果关系。",
    "confidence_interval": "此区间对应保存的估计量、组顺序与方向；无界端点不能当作零。",
    "group_means": "各组均值、样本数及标准差为描述结果；组标准差不是均值差置信区间。",
    "scores": "分数来自保存的评估协议；不同协议不能直接排名，不能据此证明外部泛化。",
}
PATHS_V1 = {
    "status": "status",
    "statistic": "result.statistic.value",
    "p_value": "result.p_value",
    "confidence_interval": "result.confidence_interval",
    "group_means": "result.descriptive_statistics",
    "scores": "comparison.rows.primary_value",
}


def _values(snapshot, facts):
    run = snapshot.document["run"]
    common = dict(
        kind=snapshot.kind,
        project_id=str(snapshot.project_id),
        dataset_id=snapshot.datasets[0]["id"],
        dataset_sha256=snapshot.datasets[0]["sha256"],
        schema_sha256=facts["schema_sha256"],
        run_id=str(snapshot.source_id),
        status=run["status"],
    )
    if snapshot.kind == "statistics":
        if len(snapshot.document["results"]) != 1:
            raise failure("READER_FIELD_UNPROVEN")
        result = snapshot.document["results"][0]
        expected = dict(
            common,
            method=run["method"],
            n=run["sample_size"],
            statistic_name=result["statistic_name"],
            statistic=result["statistic_value"],
            p_value=result["p_value"],
        )
        values = {k: expected[k] for k in ("status", "n", "statistic", "p_value")}
        values.update(
            confidence_interval=result.get("confidence_interval"),
            group_means=result.get("descriptive_statistics")
            if run["method"] == "welch_t"
            else None,
        )
    elif snapshot.kind == "ml":
        comparison = run.get("comparison") or {}
        scores = [
            dict(model_id=row.get("model_id"), score=row.get("primary_value"))
            for row in comparison.get("rows", [])
            if isinstance(row, dict) and isinstance(row.get("primary_value"), (int, float))
        ]
        expected = dict(
            common,
            n=run["n_observations"],
            primary_metric=comparison.get("primary_metric"),
            scores=scores,
        )
        values = {k: expected[k] for k in ("status", "n", "scores")}
    else:
        raise failure("READER_FIELD_UNPROVEN")
    if expected != facts:
        raise failure("READER_FIELD_UNPROVEN")
    return values


async def project(db, owner, source: reader_sources.ReaderSourceProof, request):
    """Fresh history ACL/bytes, with schema identity from the original commitment.

    Caller separately proves admission and Reply causality. No current schema,
    role or SourceVersion is substituted into a historical saved field.
    """
    try:
        return await _project(
            db,
            owner,
            source,
            reader_contracts.explanation(
                request.model_dump(mode="json"),
                reader_contracts.source_view(reader_sources.document(source)),
            ),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure("READER_FIELD_UNPROVEN") from None


async def _project(db, owner, source, request):
    document = reader_sources.document(source)
    projection_version = reader_contracts.source_view(document)
    if document["owner_id"] != str(owner):
        raise failure("READER_FIELD_UNPROVEN")
    SafeSerializer().check(document)
    selected = {}
    for item in document["results"]:
        key = (item["domain_kind"], item["domain_run_id"])
        if key in selected or not 1 <= len(item["citations"]) <= 5:
            raise failure("READER_FIELD_UNPROVEN")
        selected[key] = item
    if not 1 <= len(selected) <= 5:
        raise failure("READER_FIELD_UNPROVEN")
    cache, sources, rendered = {}, {}, []
    for reference in request.fields:
        key = (reference.kind, str(reference.run_id))
        if key not in selected:
            raise failure("TOOL_RESULT_NOT_SELECTED")
        item = selected[key]
        if key not in cache:
            if re.fullmatch(r"[0-9a-f]{64}", item["facts"]["schema_sha256"]) is None:
                raise failure("READER_FIELD_UNPROVEN")
            first = None
            for citation in item["citations"]:
                snapshot, actual = await result_contexts.prove_result(
                    db, owner, uuid.UUID(citation["artifact_id"])
                )
                if len(snapshot.datasets) != 1 or (snapshot.kind, str(snapshot.source_id)) != key:
                    raise failure("READER_FIELD_UNPROVEN")
                expected = dict(
                    actual,
                    dataset_id=snapshot.datasets[0]["id"],
                    dataset_sha256=snapshot.datasets[0]["sha256"],
                    schema_sha256=item["facts"]["schema_sha256"],
                    source_version=document["source_version"],
                )
                if citation != expected or (
                    first is not None and first.logical_hash != snapshot.logical_hash
                ):
                    raise failure("READER_FIELD_UNPROVEN")
                first = snapshot
            facts = item["facts"]
            if projection_version == 2 and first.kind == "statistics":
                descriptive = reader_saved_descriptive.project(first)
                if (
                    "descriptive_statistics" not in facts
                    or facts["descriptive_statistics"] != descriptive
                ):
                    raise failure("READER_FIELD_UNPROVEN")
                values = _values(
                    first, {k: v for k, v in facts.items() if k != "descriptive_statistics"}
                )
                values["descriptive_statistics"] = descriptive
            else:
                values = _values(first, facts)
            scope = dict(
                project_id=str(first.project_id),
                dataset_id=first.datasets[0]["id"],
                dataset_sha256=first.datasets[0]["sha256"],
                schema_sha256=item["facts"]["schema_sha256"],
                columns=result_contexts.saved_columns(first),
            )
            cache[key] = (first, values, scope)
        snapshot, values, scope = cache[key]
        if (
            reference.field not in values
            or (
                reference.field == "descriptive_statistics"
                and (
                    snapshot.kind != "statistics"
                    or snapshot.document["run"]["method"] != "descriptive"
                    or request.interpretation != "descriptive"
                )
            )
            or (
                reference.kind == "statistics"
                and (
                    request.interpretation == "prediction"
                    or (
                        request.interpretation == "mean_difference"
                        and snapshot.document["run"]["method"] != "welch_t"
                    )
                )
            )
            or (reference.kind == "ml" and request.interpretation == "mean_difference")
        ):
            raise failure("READER_FIELD_UNPROVEN")
        value = values[reference.field]
        path = PATHS_V1.get(
            reference.field, "sample_size" if reference.kind == "statistics" else "n_observations"
        )
        if reference.field == "descriptive_statistics":
            path = reader_saved_descriptive.PATH
        rendered.append(
            dict(
                **reference.model_dump(mode="json"),
                value=value,
                availability="recorded" if value is not None else "unavailable",
                text=(
                    reader_saved_descriptive.TEXT
                    if reference.field == "descriptive_statistics"
                    else TEXT_V1[reference.field]
                ),
                field_path=path,
                parameters_source="input_config"
                if reference.kind == "statistics"
                else "experiment.config",
                citations=[dict(citation, field_path=path) for citation in item["citations"]],
            )
        )
        sources[scope["dataset_id"]] = scope
    answer = dict(
        kind="validated_explanation",
        state="validated",
        request=request.model_dump(mode="json"),
        sources=list(sources.values()),
        fields=rendered,
        interpretation=request.interpretation,
        computed=False,
        limitations=["解释仅引用已选择工件的保存字段；没有重新拟合或推断因果关系。"],
    )
    SafeSerializer().check(answer)
    if len(canonical_bytes(answer)) > 128 * 1024:
        raise failure("READER_FIELD_LIMIT")
    return answer
