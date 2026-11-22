"""Agent API: list tools, create plans, execute/pause/resume/cancel plans."""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import ApprovalMode, create_plan, describe_tools
from app.database import get_db_session
from app.dependencies import get_current_user
from app.exceptions import StatForgeError
from app.models.agent import AgentSession, AgentSessionPlan, AgentSessionStep
from app.models.user import User
from app.services.project_access import owned_project

logger = logging.getLogger("statforge.agent")
router = APIRouter(tags=["agent"])


@router.get("/api/v1/agent/tools")
async def list_tools(current_user: User = Depends(get_current_user)):
    tools = describe_tools()
    return {
        "tools": tools,
        "count": len(tools),
        "available": False,
        "error_code": "AGENT_NOT_READY",
    }


@router.post("/api/v1/projects/{project_id}/agent/sessions", status_code=201)
async def create_session(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    await owned_project(db, project_id, current_user.id)
    session = AgentSession(project_id=project_id, created_by=current_user.email)
    db.add(session)
    await db.flush()
    return {"session_id": str(session.id), "project_id": str(project_id)}


@router.post("/api/v1/agent/sessions/{session_id}/plans", status_code=201)
async def create_session_plan(
    session_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    session = await db.get(AgentSession, session_id)
    if not session:
        raise StatForgeError(404, "SESSION_NOT_FOUND", "Agent session not found.")
    await owned_project(db, session.project_id, current_user.id)

    goal = body.get("user_goal", "")
    steps_data = body.get("steps", [])
    tool_sequence = [{"tool": s["tool"], "parameters": s.get("parameters", {})} for s in steps_data]
    plan = create_plan(goal, tool_sequence, ApprovalMode.ADVISORY)

    db_plan = AgentSessionPlan(
        session_id=session.id,
        user_goal=goal,
        status=plan.status.value,
        total_steps=len(plan.steps),
        snapshot_json=json.dumps(plan.to_dict(), ensure_ascii=False),
    )
    db.add(db_plan)
    await db.flush()
    for i, step in enumerate(plan.steps):
        db.add(
            AgentSessionStep(
                plan_id=db_plan.id,
                step_index=i,
                tool=step.tool,
                parameters_json=json.dumps(step.parameters, ensure_ascii=False),
            )
        )
    await db.flush()
    return {
        "plan_id": str(db_plan.id),
        "status": db_plan.status,
        "total_steps": db_plan.total_steps,
    }


@router.post("/api/v1/agent/plans/{plan_id}/execute")
async def execute_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    db_plan = await db.get(AgentSessionPlan, plan_id)
    if not db_plan:
        raise StatForgeError(404, "PLAN_NOT_FOUND", "Plan not found.")
    session = await db.get(AgentSession, db_plan.session_id)
    if not session:
        raise StatForgeError(404, "SESSION_NOT_FOUND", "Session not found.")
    await owned_project(db, session.project_id, current_user.id)
    if db_plan.status in {"cancelled", "completed", "failed"}:
        raise StatForgeError(
            409,
            "PLAN_NOT_EXECUTABLE",
            "Agent plans in a terminal state cannot be executed again.",
        )
    raise StatForgeError(
        503,
        "AGENT_NOT_READY",
        "Agent tool adapters are not available in this release.",
    )


@router.get("/api/v1/agent/plans/{plan_id}")
async def get_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    db_plan = await db.get(AgentSessionPlan, plan_id)
    if not db_plan:
        raise StatForgeError(404, "PLAN_NOT_FOUND", "Plan not found.")
    session = await db.get(AgentSession, db_plan.session_id)
    if not session:
        raise StatForgeError(404, "SESSION_NOT_FOUND", "Session not found.")
    await owned_project(db, session.project_id, current_user.id)
    if (
        db_plan.status == "completed"
        or db_plan.completed_steps > 0
        or any(step.status == "completed" or step.result_json for step in db_plan.steps)
    ):
        raise StatForgeError(
            503,
            "AGENT_NOT_READY",
            "Stored Agent results are unavailable until verified production adapters exist.",
        )
    return {
        "plan_id": str(db_plan.id),
        "user_goal": db_plan.user_goal,
        "status": db_plan.status,
        "total_steps": db_plan.total_steps,
        "completed": db_plan.completed_steps,
        "failed": db_plan.failed_steps,
        "steps": [
            {
                "tool": s.tool,
                "status": s.status,
                "result": json.loads(s.result_json) if s.result_json else None,
                "error": s.error,
            }
            for s in sorted(db_plan.steps, key=lambda s: s.step_index)
        ],
    }
