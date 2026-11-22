"""Reauthorized history and trusted renderers for the restricted research workspace."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.preview import PreviewTurn
from app.models.user import User
from app.preview.context import canonical, invalid, ml_facts, resolve_context, statistical_facts
from app.routers import statistics as stat_routes
from app.schemas.preview import PreviewScope
from app.schemas.preview_extensions import ExplanationRequest


def without_session(scope: PreviewScope) -> PreviewScope:
    return scope.model_copy(update={"session_id": None, "session_revision": None})


def history_binding(scope: PreviewScope) -> str:
    # Config is editable between turns. References and selected columns remain exact.
    return canonical(
        without_session(scope).model_dump(mode="json", exclude={"statistical_config", "ml_config"})
    )


async def session_categories(db: AsyncSession, scope: PreviewScope, user: User) -> dict[str, Any]:
    from app.preview.workspace import read_record
    from app.schemas.workspace import SessionBody

    if scope.session_id is None or scope.session_revision is None:
        raise invalid()
    row = await read_record(db, scope.session_id, user, scope.session_revision)
    if row["project_id"] != str(scope.project_id) or row["payload"]["kind"] != "session":
        raise invalid()
    session = SessionBody.model_validate(row["payload"])
    matches = history_binding(session.scope) == history_binding(scope)
    messages = []
    if matches:
        for message in session.messages:
            if history_binding(message.scope) != history_binding(scope):
                continue
            # Recheck terminal state at projection time, including legacy revisions.
            status = await db.scalar(
                select(PreviewTurn.status).where(
                    PreviewTurn.id == message.turn_id,
                    PreviewTurn.user_id == user.id,
                    PreviewTurn.project_id == scope.project_id,
                    PreviewTurn.request_id == message.request_id,
                )
            )
            if status != "finished":
                continue
            # Current ACL, data/schema SHA, Run binding and reviewed PDF versions
            # are checked again before even the historical question is projected.
            await resolve_context(db, without_session(message.scope), user)
            facts = []
            if message.scope.statistical_run_id:
                facts.append(
                    await statistical_facts(
                        db, message.scope, user, message.scope.statistical_run_id
                    )
                )
            if message.scope.ml_run_id:
                facts.append(await ml_facts(db, message.scope, user, message.scope.ml_run_id))
            messages.append(
                {"question": message.question, "request_id": message.request_id, "facts": facts}
            )
    return {
        "history": {
            "session_id": row["id"],
            "revision": row["revision"],
            "messages": messages[-12:],
        },
        "rules": {
            "confirmed_information": session.confirmed_information if matches else "",
            "personal_preferences": session.personal_preferences if matches else "",
            "project_rules": session.project_rules if matches else "",
            "task_requirements": session.task_requirements if matches else "",
            "source_match": matches,
        },
    }


async def explain_fields(
    db: AsyncSession,
    scope: PreviewScope,
    user: User,
    request: ExplanationRequest,
    allowed_runs: set[uuid.UUID],
    include_citations: bool = False,
) -> dict[str, Any]:
    rendered = []
    for reference in request.fields:
        if reference.run_id not in allowed_runs:
            raise invalid()
        if reference.kind == "statistics":
            fact = await statistical_facts(db, scope, user, reference.run_id)
            run = await stat_routes.get_statistical_run(reference.run_id, db, user)
            result = run.result
            values = {
                "status": fact["status"],
                "n": fact["n"],
                "statistic": fact["statistic"],
                "p_value": fact["p_value"],
                "confidence_interval": result.confidence_interval if result else None,
                "group_means": result.descriptive_statistics
                if result and run.method == "welch_t"
                else None,
            }
            if reference.field == "scores" or request.interpretation == "prediction":
                raise invalid()
            if request.interpretation == "mean_difference" and run.method != "welch_t":
                raise invalid()
        else:
            fact = await ml_facts(db, scope, user, reference.run_id)
            values = {"status": fact["status"], "n": fact["n"], "scores": fact["scores"]}
            if reference.field not in values or request.interpretation == "mean_difference":
                raise invalid()
        value = values.get(reference.field)
        text = {
            "status": "业务状态以保存的 Run 为准；会话结束不能代替计算完成。",
            "n": "有效样本数来自本次计算；缺失处理和筛选会改变样本量。",
            "statistic": "统计量来自保存的计算记录，需结合检验假设解释。",
            "p_value": "p 值衡量指定零假设下数据的不相容程度；不能证明效应大小或因果关系。",
            "confidence_interval": "此区间对应保存的估计量、组顺序与方向；无界端点不能当作零。",
            "group_means": "各组均值、样本数及标准差为描述结果；组标准差不是均值差置信区间。",
            "scores": "分数来自保存的评估协议；不同协议不能直接排名，不能据此证明外部泛化。",
        }[reference.field]
        rendered.append(
            {
                **reference.model_dump(mode="json"),
                "value": value,
                "availability": "recorded" if value is not None else "unavailable",
                "text": text,
                "field_path": {
                    "status": "status",
                    "n": "sample_size" if reference.kind == "statistics" else "n_observations",
                    "statistic": "result.statistic.value",
                    "p_value": "result.p_value",
                    "confidence_interval": "result.confidence_interval",
                    "group_means": "result.descriptive_statistics",
                    "scores": "comparison.rows.primary_value",
                }[reference.field],
                "parameters_source": "input_config"
                if reference.kind == "statistics"
                else "experiment.config",
            }
        )
    return {
        "kind": "validated_explanation",
        "state": "validated",
        "request": request.model_dump(mode="json"),
        "source": scope.model_dump(
            mode="json",
            include={"project_id", "dataset_id", "dataset_sha256", "schema_sha256", "columns"},
        ),
        "fields": rendered,
        "interpretation": request.interpretation,
        "limitations": ["解释引用保存字段；未推断因果关系或未计算的数值。"],
        "citations": (await resolve_context(db, without_session(scope), user))["categories"].get(
            "materials", []
        )
        if include_citations
        else [],
    }
