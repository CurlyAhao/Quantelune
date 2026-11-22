"""Fixed accepted DAG dispatch and exact historical facts; native work stays original."""

import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.models.research import ResearchArtifact, ResearchArtifactEdge, ResearchAttempt
from app.models.research_execution import ResearchPublicationReceipt
from app.models.research_planning import ResearchPlanAcceptance, ResearchPlanStep
from app.research import execution_lease, plan_acceptance, plan_receipt, plan_versioning
from app.research.contracts import State, canonical_bytes, logical_hash, parse_document
from app.services import research_capability_service as capabilities
from app.services import research_context_service as contexts
from app.services import research_service as ledger

BOUND = "plan.step_bound"
PROVEN = "plan.step_proven"
CONTROL = "plan.execution_control"


async def load(db, owner, identity, *, live=False, replay=True):
    row = await db.get(ResearchPlanAcceptance, identity, populate_existing=True)
    if row is None or row.owner_id != owner:
        raise ledger.error("PLAN_ACCEPTANCE_UNAVAILABLE", 404)
    _, scope = await ledger.session(db, row.session_id, owner, mode="live" if live else "history")
    if replay:
        from app.research.replay import verify_business

        await verify_business(db, owner, scope.id)
    _, _, saved = await plan_acceptance.version(db, owner, row.session_id, row.plan_version_id)
    plan = plan_versioning.parse(saved["plan"])
    rows = (
        await db.scalars(
            select(ResearchPlanStep)
            .where(ResearchPlanStep.acceptance_id == identity)
            .execution_options(populate_existing=True)
        )
    ).all()
    steps = {step.step_id: step for step in rows}
    if len(rows) != len(plan.steps) or set(steps) != {step.id for step in plan.steps}:
        raise ledger.error("PLAN_EXECUTION_UNPROVEN")
    return row, scope, plan, steps


async def native_citation(db, owner, acceptance, step, attempt_id):
    """Whole publication plus exact root/children identity, not current/latest pointers."""
    from app.research.domain_results import verify_artifact

    run, _ = await ledger.run(db, step.child_run_id, owner)
    _, _, saved = await plan_acceptance.version(
        db, owner, acceptance.session_id, acceptance.plan_version_id
    )
    from app.research.context_contracts import ContextSnapshot

    expected = capabilities.native_request(
        plan_versioning.parse(saved["plan"]),
        step.step_id,
        ContextSnapshot.model_validate(saved["context"]),
        protocol_snapshot=saved.get("protocol"),
    )
    captured = parse_document(run.input_json)
    if (
        run.kind != expected["kind"]
        or captured["parameters"] != expected["parameters"]
        or captured["attachments"] != saved["attachments"]
    ):
        raise ledger.error("PLAN_CHILD_CHANGED")
    attempt = await db.get(ResearchAttempt, attempt_id, populate_existing=True)
    if (
        run.session_id != acceptance.session_id
        or attempt is None
        or attempt.run_id != run.id
        or attempt.state != State.COMPLETED.value
        or attempt.domain_kind != run.kind
        or attempt.domain_run_id is None
        or attempt.completion_hash is None
    ):
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    snapshot = await ledger._snapshot_for_attempt(db, owner, run, attempt)
    view = SimpleNamespace(
        id=run.id,
        session_id=run.session_id,
        kind=run.kind,
        input_json=run.input_json,
        input_hash=run.input_hash,
        attempt_count=attempt.number,
        domain_kind=attempt.domain_kind,
        domain_run_id=attempt.domain_run_id,
    )
    if (
        snapshot.state != State.COMPLETED
        or snapshot.logical_hash != attempt.completion_hash
        or await ledger._prove_completion(db, owner, view, current_context=False)
        != attempt.completion_hash
    ):
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    publication = await db.get(ResearchPublicationReceipt, attempt.id, populate_existing=True)
    root = (
        await db.get(ResearchArtifact, publication.root_id, populate_existing=True)
        if publication
        else None
    )
    if root is None or root.kind != "domain_result":
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    children = (
        await db.scalars(
            select(ResearchArtifact)
            .join(ResearchArtifactEdge, ResearchArtifactEdge.child_id == ResearchArtifact.id)
            .where(
                ResearchArtifactEdge.parent_id == root.id,
                ResearchArtifactEdge.relation == "saved_output",
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    if len({child.object_key for child in children}) != len(children) or {
        child.object_key for child in children
    } != {output.object_key for output in snapshot.outputs}:
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    artifacts = [root] + children
    for artifact in artifacts:
        provenance = parse_document(artifact.provenance_json)
        if artifact.run_id != run.id or provenance.get("attempt_number", 1) != attempt.number:
            raise ledger.error("PLAN_RESULT_UNPROVEN")
        verify_artifact(artifact, snapshot, owner)  # Actual streamed byte proof, all outputs.
    definition = parse_document(step.document_json)["step"]
    return dict(
        kind="native_result",
        run_id=str(run.id),
        attempt_id=str(attempt.id),
        attempt_number=attempt.number,
        completion_hash=attempt.completion_hash,
        input_hash=run.input_hash,
        domain_kind=attempt.domain_kind,
        domain_run_id=str(attempt.domain_run_id),
        dataset_id=definition["dataset_id"],
        dataset_sha256=definition["dataset_sha256"],
        artifacts=[
            dict(
                id=str(item.id),
                sha256=item.sha256,
                logical_hash=item.logical_hash,
                bytes=item.byte_size,
                media_type=item.media_type,
            )
            for item in sorted(artifacts, key=lambda item: str(item.id))
        ],
    )


async def authorize_child(db, owner, run, *, stage=None):
    """New claim/intention/first dispatch only; never gates existing result closeout."""
    step = await db.scalar(
        select(ResearchPlanStep)
        .where(ResearchPlanStep.child_run_id == run.id)
        .execution_options(populate_existing=True)
    )
    if step is None:
        if "p4_protocol" in parse_document(run.input_json)["parameters"]:
            raise ledger.error("PLAN_CHILD_CHANGED")
        return  # Existing independent/manual native Runs retain their contract.
    acceptance, _, plan, steps = await load(db, owner, step.acceptance_id, live=True)
    await plan_receipt._actor(db, owner)
    if (
        acceptance.cancel_requested
        or step.result_hash is not None
        or run.session_id != acceptance.session_id
    ):
        raise ledger.error("PLAN_EXECUTION_STOPPED")
    context = await contexts.snapshot(
        db, owner, run.session_id, expected_source_version=plan.source_version
    )
    plan, protocol_snapshot = await plan_versioning.live(db, owner, run.session_id, plan, context)
    request = capabilities.native_request(
        plan, step.step_id, context, protocol_snapshot=protocol_snapshot
    )
    captured = parse_document(run.input_json)
    if (
        run.kind != request["kind"]
        or captured["parameters"] != request["parameters"]
        or logical_hash(captured) != run.input_hash
    ):
        raise ledger.error("PLAN_CHILD_CHANGED")
    selected = next(item for item in plan.steps if item.id == step.step_id)
    for dependency in selected.depends_on:
        await prove_step(db, owner, acceptance, steps[dependency])
    if plan_versioning.is_v2(plan) and stage is not None:
        from app.research import protocol_launch

        await protocol_launch.record(db, owner, run, acceptance, step, plan, stage)


async def prove_step(db, owner, acceptance, step):
    if step.result_json is None or step.result_hash is None:
        raise ledger.error("PLAN_DEPENDENCY_UNPROVEN")
    value = parse_document(step.result_json)
    if (
        set(value)
        != {
            "version",
            "acceptance_id",
            "acceptance_hash",
            "plan_version_id",
            "plan_hash",
            "source_version",
            "step_id",
            "step_document_hash",
            "citation",
        }
        or value["version"] != 1
        or step.result_hash != logical_hash(value)
        or value["acceptance_id"] != str(acceptance.id)
        or value["acceptance_hash"] != acceptance.document_hash
        or value["plan_version_id"] != str(acceptance.plan_version_id)
        or value["step_id"] != step.step_id
        or value["step_document_hash"] != step.document_hash
    ):
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    _, _, saved = await plan_acceptance.version(
        db, owner, acceptance.session_id, acceptance.plan_version_id
    )
    if (
        value["plan_hash"] != saved["plan_hash"]
        or value["source_version"] != saved["plan"]["source_version"]
    ):
        raise ledger.error("PLAN_RESULT_UNPROVEN")
    if step.child_run_id is not None:
        if step.result_attempt_id is None or value["citation"] != await native_citation(
            db, owner, acceptance, step, step.result_attempt_id
        ):
            raise ledger.error("PLAN_RESULT_UNPROVEN")
    else:
        if step.result_attempt_id is not None:
            raise ledger.error("PLAN_RESULT_UNPROVEN")
        await prove_read(db, owner, acceptance, step, value["citation"])
    return value


async def prove_read(db, owner, acceptance, step, citation):
    """Saved immutable context or a fixed actual Profile, not a new current read."""
    from app.models.profiling import DatasetProfile
    from app.research.context_contracts import ContextSnapshot

    _, _, saved = await plan_acceptance.version(
        db, owner, acceptance.session_id, acceptance.plan_version_id
    )
    context = ContextSnapshot.model_validate(saved["context"])
    definition = parse_document(step.document_json)["step"]
    source = next(
        item for item in context.datasets if str(item.dataset_id) == definition["dataset_id"]
    )
    if definition["capability"] == "inspect":
        expected = dict(
            state="read",
            capability="inspect",
            dataset=source.model_dump(mode="json"),
            citation=dict(
                kind="dataset_version",
                id=str(source.dataset_id),
                sha256=source.sha256,
                schema_sha256=source.schema_sha256,
                source_version=context.source_version,
            ),
            computed=False,
        )
        if citation != expected:
            raise ledger.error("PLAN_RESULT_UNPROVEN")
    elif definition["capability"] == "profile":
        reference = citation["citation"]
        from sqlalchemy import func

        profile = await db.scalar(
            select(DatasetProfile)
            .where(
                DatasetProfile.id == uuid.UUID(reference["id"]),
                func.length(DatasetProfile.result_json) <= 1024 * 1024,
            )
            .execution_options(populate_existing=True)
        )
        if (
            profile is None
            or profile.status.value != "completed"
            or profile.dataset_id != source.dataset_id
            or profile.dataset_sha256 != source.sha256
            or profile.algorithm_version != reference["algorithm_version"]
            or profile.profiling_version != reference["profiling_version"]
            or logical_hash(parse_document(profile.result_json)) != reference["result_hash"]
        ):
            raise ledger.error("PLAN_RESULT_UNPROVEN")
        expected = capabilities.profile_projection(
            parse_document(profile.result_json),
            {item["name"] for item in definition["fields"]}
            or {item.reference.name for item in source.fields},
        )
        expected_document = dict(
            state="read",
            capability="profile",
            facts=expected,
            computed=False,
            citation=dict(
                kind="saved_profile",
                id=str(profile.id),
                dataset_id=str(source.dataset_id),
                dataset_sha256=profile.dataset_sha256,
                algorithm_version=profile.algorithm_version,
                profiling_version=profile.profiling_version,
                result_hash=logical_hash(parse_document(profile.result_json)),
            ),
        )
        if citation != expected_document:
            raise ledger.error("PLAN_RESULT_UNPROVEN")
    else:
        raise ledger.error("PLAN_RESULT_UNPROVEN")


async def record_result(db, owner, acceptance, scope, plan, step, citation, attempt_id=None):
    value = dict(
        version=1,
        acceptance_id=str(acceptance.id),
        acceptance_hash=acceptance.document_hash,
        plan_version_id=str(acceptance.plan_version_id),
        plan_hash=logical_hash(plan.model_dump(mode="json")),
        source_version=plan.source_version,
        step_id=step.step_id,
        step_document_hash=step.document_hash,
        citation=citation,
    )
    if step.result_json is not None:
        if parse_document(step.result_json) != value or step.result_attempt_id != attempt_id:
            raise ledger.error("PLAN_RESULT_CHANGED")
        return
    step.result_attempt_id = attempt_id
    step.result_json = canonical_bytes(value).decode()
    step.result_hash = logical_hash(value)
    await db.flush()
    await ledger._append(db, scope, owner, "plan_step", step.id, PROVEN, value)


async def advance(db, owner, identity):
    """One short fenced pass; compute stays in the existing native supervisor."""
    from app.models.research import ResearchRun

    await execution_lease._boundary(db, owner)
    await plan_receipt._actor(db, owner)
    acceptance, scope, plan, steps = await load(db, owner, identity)
    recorded = False
    for step in steps.values():
        if step.child_run_id is None or step.result_json is not None:
            continue
        run, _ = await ledger.run(db, step.child_run_id, owner)
        if run.state == State.COMPLETED.value:
            attempt = await db.scalar(
                select(ResearchAttempt).where(
                    ResearchAttempt.run_id == run.id, ResearchAttempt.number == run.attempt_count
                )
            )
            citation = await native_citation(db, owner, acceptance, step, attempt.id)
            await record_result(db, owner, acceptance, scope, plan, step, citation, attempt.id)
            recorded = True
    if recorded or all(step.result_hash is not None for step in steps.values()):
        # Existing actual output commits independently of permission for later work.
        return await status(db, owner, acceptance, plan, steps)
    if acceptance.cancel_requested:
        return await status(db, owner, acceptance, plan, steps)
    await ledger.session(db, acceptance.session_id, owner, mode="live")
    context = await contexts.snapshot(
        db, owner, acceptance.session_id, expected_source_version=plan.source_version
    )
    plan, protocol_snapshot = await plan_versioning.live(
        db, owner, acceptance.session_id, plan, context
    )
    for definition in plan.steps:
        step = steps[definition.id]
        if step.child_run_id is not None or step.result_json is not None:
            continue
        if any(steps[name].result_hash is None for name in definition.depends_on):
            continue
        dependencies = {
            name: (await prove_step(db, owner, acceptance, steps[name]))
            for name in definition.depends_on
        }
        if definition.capability in {"inspect", "profile"}:
            citation = await capabilities.read_step(
                db, owner, acceptance.session_id, plan, definition.id
            )
            if citation["state"] != "read":
                continue  # Real profile unavailable: no fabricated result or dependent launch.
            await record_result(db, owner, acceptance, scope, plan, step, citation)
        else:
            request = capabilities.native_request(
                plan, definition.id, context, protocol_snapshot=protocol_snapshot
            )
            key = "plan-child:" + logical_hash(
                dict(acceptance_id=str(identity), step_id=step.step_id)
            )
            collision = await db.scalar(
                select(ResearchRun.id).where(
                    ResearchRun.session_id == acceptance.session_id,
                    ResearchRun.idempotency_key == key,
                )
            )
            if collision is not None:
                raise ledger.error("PLAN_CHILD_COLLISION")  # Never adopt an unrelated/manual Run.
            child = await ledger.start_run(
                db, owner, acceptance.session_id, request["kind"], key, request["parameters"]
            )
            step.child_run_id = child.id
            await db.flush()
            value = dict(
                version=1,
                acceptance_id=str(identity),
                acceptance_hash=acceptance.document_hash,
                step_id=step.step_id,
                step_document_hash=step.document_hash,
                run_id=str(child.id),
                input_hash=child.input_hash,
                dependencies={
                    name: logical_hash(receipt) for name, receipt in dependencies.items()
                },
            )
            await ledger._append(db, scope, owner, "plan_step", step.id, BOUND, value)
    return await status(db, owner, acceptance, plan, steps)


async def status(db, owner, acceptance, plan, steps):
    values = []
    for definition in plan.steps:
        step = steps[definition.id]
        if step.result_hash is not None:
            result = await prove_step(db, owner, acceptance, step)
            state = "proven"
        else:
            result = None
            if step.child_run_id is not None:
                run, _ = await ledger.run(db, step.child_run_id, owner)
                state = run.state
            else:
                state = (
                    "not_started"
                    if all(steps[name].result_hash is not None for name in definition.depends_on)
                    else "dependency_blocked"
                )
        values.append(
            dict(
                step_id=step.step_id,
                state=state,
                child_run_id=str(step.child_run_id) if step.child_run_id else None,
                result=result,
            )
        )
    complete = all(item["state"] == "proven" for item in values)
    stopping = acceptance.cancel_requested and any(
        item["state"] in {"queued", "running", "paused", "waiting_approval", "cancel_requested"}
        for item in values
    )
    return dict(
        acceptance_id=str(acceptance.id),
        plan_version_id=str(acceptance.plan_version_id),
        source_version=plan.source_version,
        revision=acceptance.revision,
        cancel_requested=acceptance.cancel_requested,
        state="completed"
        if complete
        else "cancel_pending"
        if stopping
        else "cancelled"
        if acceptance.cancel_requested
        else "incomplete",
        steps=values,
    )


async def cancel(db, owner, identity, revision):
    from app.research import execution_control

    await execution_lease._boundary(db, owner)
    await plan_receipt._actor(db, owner)
    acceptance, scope, plan, steps = await load(db, owner, identity)
    if acceptance.revision != revision:
        raise ledger.error("STALE_REVISION")
    if all(step.result_hash is not None for step in steps.values()):
        return await status(db, owner, acceptance, plan, steps)
    if not acceptance.cancel_requested:
        before = acceptance.revision
        acceptance.cancel_requested = True
        acceptance.revision += 1
        await ledger._append(
            db,
            scope,
            owner,
            "plan_acceptance",
            identity,
            CONTROL,
            dict(
                version=1,
                action="cancel",
                acceptance_hash=acceptance.document_hash,
                revision_before=before,
                revision_after=acceptance.revision,
            ),
        )
    for step in steps.values():
        if step.child_run_id is None or step.result_json is not None:
            continue
        run, _ = await ledger.run(db, step.child_run_id, owner)
        if run.state in {"queued", "running", "paused", "waiting_approval"}:
            from app.exceptions import StatForgeError

            try:
                await execution_control.request(db, owner, run.id, run.revision, "cancel")
            except StatForgeError:
                # Consent revocation still commits. Retained control may require
                # the original worker/reconciliation; pending state remains honest.
                continue
    await db.flush()
    return await status(db, owner, acceptance, plan, steps)


async def verify_event(db, owner, event, saved, acceptances, controls, children, results, runs):
    value = parse_document(event.payload_json)
    if event.actor_id != owner or event.logical_hash != logical_hash(value):
        raise ValueError("Plan execution event identity changed.")
    if event.event_type == CONTROL:
        acceptance = await db.get(ResearchPlanAcceptance, event.entity_id, populate_existing=True)
        if (
            event.entity_kind != "plan_acceptance"
            or event.entity_id not in acceptances
            or acceptance is None
            or set(value)
            != {"version", "action", "acceptance_hash", "revision_before", "revision_after"}
            or value["version"] != 1
            or value["action"] != "cancel"
            or value["acceptance_hash"] != acceptance.document_hash
        ):
            raise ValueError("Plan control event route changed.")
        previous = controls[event.entity_id]
        if (
            previous["cancel_requested"]
            or type(value["revision_before"]) is not int
            or value["revision_before"] != previous["revision"]
            or value["revision_after"] != previous["revision"] + 1
        ):
            raise ValueError("Plan control CAS history changed.")
        controls[event.entity_id] = dict(revision=value["revision_after"], cancel_requested=True)
        return
    step = await db.get(ResearchPlanStep, event.entity_id, populate_existing=True)
    if (
        event.entity_kind != "plan_step"
        or step is None
        or step.acceptance_id not in acceptances
        or value["acceptance_id"] != str(step.acceptance_id)
        or value["step_id"] != step.step_id
        or value["step_document_hash"] != step.document_hash
    ):
        raise ValueError("Plan step event route changed.")
    acceptance = await db.get(ResearchPlanAcceptance, step.acceptance_id, populate_existing=True)
    plan = plan_versioning.parse(saved[str(acceptance.plan_version_id)]["plan"])
    definition = next(item for item in plan.steps if item.id == step.step_id)
    rows = (
        await db.scalars(
            select(ResearchPlanStep).where(ResearchPlanStep.acceptance_id == acceptance.id)
        )
    ).all()
    identifiers = {row.step_id: row.id for row in rows}
    dependency_results = {name: results.get(identifiers[name]) for name in definition.depends_on}
    if any(receipt is None for receipt in dependency_results.values()):
        raise ValueError("Dependent step precedes real dependency receipt.")
    if event.event_type == BOUND:
        from app.models.research import ResearchRun
        from app.research.context_contracts import ContextSnapshot

        if (
            set(value)
            != {
                "version",
                "acceptance_id",
                "acceptance_hash",
                "step_id",
                "step_document_hash",
                "run_id",
                "input_hash",
                "dependencies",
            }
            or value["version"] != 1
            or event.entity_id in children
            or event.entity_id in results
            or controls[acceptance.id]["cancel_requested"]
            or value["acceptance_hash"] != acceptance.document_hash
            or value["dependencies"]
            != {name: logical_hash(receipt) for name, receipt in dependency_results.items()}
        ):
            raise ValueError("Plan child binding history changed.")
        run_id = uuid.UUID(value["run_id"])
        run = await db.get(ResearchRun, run_id, populate_existing=True)
        projection = runs.get(run_id)
        request = capabilities.native_request(
            plan,
            step.step_id,
            ContextSnapshot.model_validate(saved[str(acceptance.plan_version_id)]["context"]),
            protocol_snapshot=saved[str(acceptance.plan_version_id)].get("protocol"),
        )
        if (
            run is None
            or projection is None
            or projection["state"] != "queued"
            or projection["attempt_count"] != 1
            or run.session_id != acceptance.session_id
            or run.kind != request["kind"]
            or run.input_hash != value["input_hash"]
            or parse_document(run.input_json)["parameters"] != request["parameters"]
            or run.idempotency_key
            != "plan-child:"
            + logical_hash(dict(acceptance_id=str(acceptance.id), step_id=step.step_id))
        ):
            raise ValueError("Plan actual child Run binding changed.")
        children[step.id] = run.id
    elif event.event_type == PROVEN:
        if step.id in results or value["acceptance_hash"] != acceptance.document_hash:
            raise ValueError("Duplicate or changed Plan result.")
        if value["citation"].get("kind") == "native_result":
            run_id = children.get(step.id)
            projection = runs.get(run_id)
            if (
                projection is None
                or projection["state"] != "completed"
                or projection["attempt_count"] != value["citation"]["attempt_number"]
                or value["citation"]["run_id"] != str(run_id)
            ):
                raise ValueError("Native step proof precedes actual child completion.")
        elif step.id in children or controls[acceptance.id]["cancel_requested"]:
            raise ValueError("Unpermitted Plan read result.")
        if await prove_step(db, owner, acceptance, step) != value:
            raise ValueError("Plan real result changed.")
        results[step.id] = value
    else:
        raise ValueError("Unknown Plan execution event.")
