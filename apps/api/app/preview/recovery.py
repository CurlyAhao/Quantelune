"""Finite restart recovery: inspect true effects, never replay pending operations."""

import json
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StatForgeError
from app.models.preview import PreviewEffect, PreviewTurn
from app.models.user import User
from app.preview import vault as credentials
from app.preview.configuration import get_config, not_ready
from app.preview.context import (
    canonical,
    invalid,
    load_dataset,
    ml_facts,
    resolve_context,
    statistical_facts,
    verify_storage_version,
)
from app.preview.harness import DOMAIN_TOOLS
from app.preview.read_evidence import (
    EVIDENCE_LIMIT,
    ML_KEYS,
    READ_LIMIT,
    RECEIPTS,
    STAT_KEYS,
    validate_record,
)
from app.preview.security import contains_secret
from app.preview.service import response
from app.schemas.preview import PreviewScope
from app.services.workflow_service import check_project_access


async def mark_interrupted(db: AsyncSession) -> int:
    """Only the single-instance preview bootstrap invokes this after acquiring its lock."""
    rows = await db.execute(
        update(PreviewTurn)
        .where(PreviewTurn.status == "running")
        .values(status="interrupted", error_code="PREVIEW_RESTART_INTERRUPTED")
        .returning(PreviewTurn.id)
    )
    count = len(rows.all())
    await db.commit()
    return count


async def read_saved(db: AsyncSession, turn_id: uuid.UUID, user: User) -> dict[str, Any]:
    with db.no_autoflush:
        identity = (
            await db.execute(
                select(PreviewTurn.user_id, PreviewTurn.project_id).where(PreviewTurn.id == turn_id)
            )
        ).one_or_none()
    if identity is None or identity.user_id != user.id:
        raise StatForgeError(404, "PREVIEW_TURN_NOT_FOUND", "Turn unavailable.")
    await check_project_access(db, identity.project_id, user)
    row = await db.get(PreviewTurn, turn_id)
    assert row is not None
    scope = PreviewScope.model_validate_json(row.scope_json)
    context = await resolve_context(db, scope, user)
    if scope.dataset_id is not None:
        await verify_storage_version(await load_dataset(db, scope, user))
    effects = list(
        (
            await db.scalars(
                select(PreviewEffect)
                .where(PreviewEffect.turn_id == turn_id)
                .order_by(PreviewEffect.created_at, PreviewEffect.id)
            )
        ).all()
    )
    try:
        saved = json.loads(row.response_json)
        authorization = json.loads(row.authorization_json)
    except ValueError:
        raise invalid() from None
    if not isinstance(saved, list) or not isinstance(authorization, dict):
        raise invalid()
    configuration = await get_config(db, user.id)
    secret = None
    if configuration and configuration.key_configured:
        secret = credentials.vault.get(configuration.vault_namespace, user.id)
        if not secret:
            raise not_ready()
        if contains_secret([saved, authorization], secret):
            raise invalid()
    if len(canonical(saved).encode()) > EVIDENCE_LIMIT:
        raise invalid()
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in saved + [json.loads(e.result_json) for e in effects if e.status == "succeeded"]:
        if not isinstance(item, dict):
            raise invalid()
        kind = item.get("kind")
        if "binding" in item:
            if context["disclosure_hash"] != authorization.get("disclosure_hash"):
                raise invalid()
            validate_record(
                item,
                scope,
                authorization.get("disclosure_hash", ""),
                authorization.get("approved_categories", []),
            )
        if kind in RECEIPTS and item.get("state") == "observed":
            if "binding" not in item:
                raise invalid()
            key = (kind, item["binding"]["evidence_digest"])
            if key not in seen:
                seen.add(key)
                facts.append(item)
            continue
        if kind in {"statistics", "ml"}:
            if set(item) != (STAT_KEYS if kind == "statistics" else ML_KEYS) | (
                {"binding"} if "binding" in item else set()
            ):
                raise invalid()
            run_id = uuid.UUID(item["run_id"])
            key = (kind, str(run_id))
            if key in seen:
                continue
            seen.add(key)
            loader = statistical_facts if kind == "statistics" else ml_facts
            fact = await loader(db, scope, user, run_id)
            if "binding" in item:
                fact["binding"] = item["binding"]
            facts.append(fact)
        elif kind == "source_references":
            if set(item) != {"kind", "citations"}:
                raise invalid()
            facts.append({"kind": kind, "citations": context["categories"].get("materials", [])})
        elif kind == "plan_proposal":
            from app.preview.workspace import read_record

            if (
                set(item) != {"kind", "state", "plan_id", "revision", "project_id"}
                or item.get("state") != "proposed"
            ):
                raise invalid()
            plan = await read_record(db, uuid.UUID(item["plan_id"]), user, item["revision"])
            if plan["project_id"] != str(scope.project_id) or plan["payload"]["kind"] != "plan":
                raise invalid()
            facts.append(item)
        elif kind == "validated_explanation":
            from app.preview.extensions import explain_fields
            from app.schemas.preview_extensions import ExplanationRequest

            if set(item) != {
                "kind",
                "state",
                "request",
                "source",
                "fields",
                "interpretation",
                "limitations",
                "citations",
            } or "results" not in authorization.get("approved_categories", []):
                raise invalid()
            allowed = {run for run in (scope.statistical_run_id, scope.ml_run_id) if run}
            allowed.update(
                uuid.UUID(json.loads(e.result_json)["run_id"])
                for e in effects
                if e.status == "succeeded"
            )
            rendered = await explain_fields(
                db,
                scope,
                user,
                ExplanationRequest.model_validate(item["request"]),
                allowed,
                "materials" in authorization.get("approved_categories", []),
            )
            if canonical(item) != canonical(rendered):
                raise invalid()
            facts.append(rendered)
        elif kind in {"statistics_proposal", "ml_proposal"}:
            if set(item) != {
                "kind",
                "project_id",
                "dataset_id",
                "schema_sha256",
                "config",
                "dataset_sha256",
                "state",
            } or item.get("state") not in {"proposed", "material_parameter_review_required"}:
                raise invalid()
            for field in ("project_id", "dataset_id", "schema_sha256", "dataset_sha256"):
                if item.get(field) != scope.model_dump(mode="json")[field]:
                    raise invalid()
            from app.schemas.preview import MLForm, StatisticalForm

            model = StatisticalForm if kind == "statistics_proposal" else MLForm
            try:
                model.model_validate(item["config"])
            except ValueError:
                raise invalid() from None
            facts.append(item)
        elif (
            kind in DOMAIN_TOOLS
            and item.get("state") == "rejected"
            and set(item) == {"kind", "state", "error_code"}
            and isinstance(item.get("error_code"), str)
            and len(item["error_code"]) <= 64
        ):
            facts.append(item)
        else:
            raise invalid()
    if secret and contains_secret([facts, authorization], secret):
        raise invalid()
    if len(canonical(facts).encode()) > EVIDENCE_LIMIT or any(
        "binding" in f and len(canonical(f).encode()) > READ_LIMIT for f in facts
    ):
        raise invalid()
    value = response(row)
    value["evidence"] = facts
    value["scope"] = scope.model_dump(mode="json")
    value["effects"] = [{"status": e.status} for e in effects]
    value["recovery_notice"] = (
        "Interrupted or pending work is not replayed; inspect true Runs before a new request."
        if row.status in {"running", "interrupted"} or any(e.status == "pending" for e in effects)
        else None
    )
    return value
