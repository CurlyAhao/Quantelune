"""Exactly three local planning tools, bounded by the approved metadata selection."""

import asyncio
import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.models.research import ResearchAttempt
from app.models.research_planning import ResearchPlanReceipt, ResearchPlanVersion
from app.research import (
    harness_binding,
    plan_receipt,
    planner_invocation,
    planning_control,
    planning_lease,
)
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash, parse_document
from app.research.egress import DisclosureSelection, failure
from app.research.harness_serde import SafeSerializer
from app.research.plan_contracts import PlanDocument, PlanStep
from app.research.plan_v2_contracts import PlanDocumentV2
from app.services import research_capability_service as capabilities
from app.services import research_context_service as contexts
from app.services import research_plan_service as plans
from app.services import research_service as ledger


class DatasetArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: uuid.UUID


class PlanArgument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: PlanDocument


class PlanArgumentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: PlanDocumentV2


def build_tools(call):
    async def inspect(dataset_id: uuid.UUID):
        return await call("inspect", dict(dataset_id=str(dataset_id)))

    async def profile(dataset_id: uuid.UUID):
        return await call("profile", dict(dataset_id=str(dataset_id)))

    async def propose_plan(plan: PlanDocument):
        return await call("propose_plan", dict(plan=plan.model_dump(mode="json")))

    return [
        StructuredTool.from_function(
            coroutine=inspect,
            name="inspect",
            description=(
                "Read approved local dataset metadata and separate field references; "
                # Exact tested schema text; keep readable source lines.
                "no rows or computation."
            ),
            args_schema=DatasetArgument,
        ),
        StructuredTool.from_function(
            coroutine=profile,
            name="profile",
            description=(
                "Read only a real saved current aggregate profile for approved fields; "
                # Exact tested schema text.
                "no raw values or new fit."
            ),
            args_schema=DatasetArgument,
        ),
        StructuredTool.from_function(
            coroutine=propose_plan,
            name="propose_plan",
            description=(
                "Store one explicit bounded source Plan proposal. "
                # Exact tested schema text.
                "User acceptance is required before any native work."
            ),
            args_schema=PlanArgument,
        ),
    ]


def build_tools_v2(call):
    async def propose_plan(plan: PlanDocumentV2):
        return await call("propose_plan", dict(plan=plan.model_dump(mode="json")))

    return [
        *build_tools(call)[:2],
        StructuredTool.from_function(
            coroutine=propose_plan,
            name="propose_plan",
            description=(
                "Store one explicit Protocol-bound research-plan-v2 proposal. "
                "User acceptance is required before any native work."
            ),
            args_schema=PlanArgumentV2,
        ),
    ]


class PlannerTools:
    def __init__(self, factory, token, *, version=1):
        self.factory, self.token = factory, token
        if version not in {1, 2}:
            raise failure("PLANNER_VERSION_UNSUPPORTED")
        self.version = version
        self.lock = asyncio.Lock()

    async def safe_call(self, name, args):
        if name not in {"inspect", "profile", "propose_plan"}:
            raise failure("PLANNER_TOOL_DENIED")
        if name == "propose_plan":
            argument = (PlanArgumentV2 if self.version == 2 else PlanArgument).model_validate(args)
            plan = argument.plan
        else:
            argument = DatasetArgument.model_validate(args)
        SafeSerializer().check(args)
        if len(canonical_bytes(args)) > 128 * 1024:
            raise failure("PLANNER_TOOL_LIMIT")
        async with self.lock:
            binding = await harness_binding.existing(self.factory, self.token, "planner")
            if binding is None:
                raise failure("INVOCATION_REQUIRED")
            async with self.factory() as db:
                run, _ = await ledger.run(db, self.token.run_id, self.token.owner_id)
                from app.research import plan_versioning, planner_v2

                snapshot = planner_v2.captured(run)
                if (snapshot is not None) != (self.version == 2):
                    raise failure("PLANNER_VERSION_UNSUPPORTED")
                attempt = await db.get(
                    ResearchAttempt, self.token.attempt_id, populate_existing=True
                )
                invocation = await planner_invocation.load(
                    db, self.token.owner_id, run, self.token.attempt_id
                )
                if (
                    invocation is None
                    or attempt is None
                    or await planning_control.cancelled(db, self.token.owner_id, run, attempt)
                ):
                    raise failure("MODEL_CANCELLED")
                receipt = await db.scalar(
                    select(ResearchPlanReceipt).where(
                        ResearchPlanReceipt.attempt_id == self.token.attempt_id
                    )
                )
                if receipt is not None:
                    await plan_receipt.prove(db, self.token.owner_id, run, attempt)
                    version = await db.get(
                        ResearchPlanVersion, receipt.plan_version_id, populate_existing=True
                    )
                    original = parse_document(version.document_json)
                    if (
                        name != "propose_plan"
                        or plan_versioning.digest(
                            plan_versioning.validate(
                                plan,
                                ContextSnapshot.model_validate(original["context"]),
                                original.get("protocol"),
                            )
                        )
                        != version.plan_hash
                    ):
                        raise failure("PLANNING_ALREADY_RECEIPTED")
                    return self.result(receipt, version)
                _, _, lease, _ = await planning_lease.validate(db, self.token)
                if lease.pause_requested:
                    raise failure("MODEL_PAUSED")
                selection = DisclosureSelection.model_validate(invocation["selection"])
                current = await contexts.snapshot(
                    db,
                    self.token.owner_id,
                    run.session_id,
                    expected_source_version=parse_document(run.input_json)["parameters"][
                        "source_version"
                    ],
                )
                allowed = {
                    uuid.UUID(item.dataset_id): set(item.columns) for item in selection.datasets
                }
                if name == "propose_plan":
                    if snapshot is not None:
                        planner_v2.selected(snapshot, selection)
                        planner_v2.plan_matches(run, plan)
                    normalized = plan_versioning.validate(plan, current, snapshot)
                    for step in normalized.steps:
                        columns = allowed.get(step.dataset_id)
                        if columns is None or not {item.name for item in step.fields} <= columns:
                            raise failure("PLANNER_SCOPE_CHANGED")
                        config = step.configuration
                        used = set(config.get("variables", [])) | set(
                            (config.get("ohlcv_mapping") or {}).values()
                        )
                        if snapshot is not None:
                            used |= set(config.get("categorical_variables") or []) | set(
                                config.get("reference_categories") or {}
                            )
                            used |= {
                                config[key]
                                for key in ("group_variable", "target")
                                if config.get(key) is not None
                            }
                        if not used <= columns:
                            raise failure("PLANNER_SCOPE_CHANGED")
                    key = "planner-proposal:" + logical_hash(
                        dict(
                            binding_hash=binding.binding_hash,
                            plan=normalized.model_dump(mode="json"),
                        )
                    )
                    saved = await plans.save(
                        db, self.token.owner_id, run.session_id, normalized, 0, key
                    )
                    receipt = await plan_receipt.record(
                        db, self.token, uuid.UUID(saved["version_id"])
                    )
                    version = await db.get(ResearchPlanVersion, receipt.plan_version_id)
                    result = self.result(receipt, version)
                    await db.commit()
                    return result
                dataset_id = argument.dataset_id
                source = next(
                    (item for item in current.datasets if item.dataset_id == dataset_id), None
                )
                if source is None or dataset_id not in allowed:
                    raise failure("PLANNER_SCOPE_CHANGED")
                fields = [
                    item.reference
                    for item in source.fields
                    if item.reference.name in allowed[dataset_id]
                ]
                selected = PlanDocument(
                    source_version=current.source_version,
                    title="Approved metadata read",
                    steps=[
                        PlanStep(
                            id="read",
                            capability=name,
                            dataset_id=dataset_id,
                            dataset_sha256=source.sha256,
                            fields=fields,
                        )
                    ],
                )
                result = await capabilities.read_step(
                    db, self.token.owner_id, run.session_id, selected, "read"
                )
                if name == "inspect":
                    result["dataset"]["fields"] = [
                        item
                        for item in result["dataset"]["fields"]
                        if item["reference"]["name"] in allowed[dataset_id]
                    ]
                SafeSerializer().check(result)
                if len(canonical_bytes(result)) > 128 * 1024:
                    raise failure("PLANNER_TOOL_LIMIT")
                return result

    @staticmethod
    def result(receipt, version):
        return dict(
            state="proposed",
            executed=False,
            plan_receipt_id=str(receipt.id),
            receipt_hash=receipt.document_hash,
            plan_version_id=str(version.id),
            plan_hash=version.plan_hash,
            citation=dict(
                kind="plan_version",
                id=str(version.id),
                document_hash=version.document_hash,
                source_version=receipt.source_version,
            ),
        )
