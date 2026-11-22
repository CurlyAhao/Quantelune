"""Agent Runtime: conversation manager, planner, policy engine, executor.

The agent stores plans and their state with checkpointing. Production tool
adapters are intentionally unavailable during P0, so execution never returns
synthetic analysis results.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalMode(str, Enum):
    ADVISORY = "advisory"  # confirm every step
    SEMI_AUTO = "semi_auto"  # read-only + analysis auto; writes + external need confirm
    MANAGED = "managed"  # approved plan runs autonomously within limits


@dataclass
class AgentStep:
    step_id: str
    tool: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    input_hash: str = ""
    output_hash: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_count: int = 0
    started_at: str = ""
    completed_at: str = ""
    approval_granted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "parameters": self.parameters,
            "status": self.status.value,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "approval_granted": self.approval_granted,
        }


@dataclass
class AgentPlan:
    plan_id: str
    user_goal: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    approval_mode: ApprovalMode = ApprovalMode.ADVISORY
    max_steps: int = 50
    step_budget: int = 100
    token_budget: int = 100000
    max_time_seconds: int = 3600
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "user_goal": self.user_goal,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status.value,
            "approval_mode": self.approval_mode.value,
            "max_steps": self.max_steps,
            "step_budget": self.step_budget,
            "token_budget": self.token_budget,
            "max_time_seconds": self.max_time_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @property
    def current_step_index(self) -> int:
        for i, s in enumerate(self.steps):
            if s.status != StepStatus.COMPLETED:
                return i
        return len(self.steps)

    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)

    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)


class PolicyEngine:
    """Check tool permissions, budget limits, data exfiltration policies."""

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.ADVISORY,
        allowed_tools: set[str] | None = None,
        disallowed_tools: set[str] | None = None,
        token_budget: int = 100000,
        max_time_seconds: int = 3600,
        max_steps: int = 50,
        data_export_policy: str = "sanitized_sample",
    ) -> None:
        self.mode = mode
        self.allowed_tools = allowed_tools or set()
        self.disallowed_tools = disallowed_tools or set()
        self.token_budget = token_budget
        self.max_time_seconds = max_time_seconds
        self.max_steps = max_steps
        self.data_export_policy = (
            data_export_policy  # metadata_only | sanitized_sample | raw_data_approved
        )

    def can_use_tool(self, tool_name: str, spec: dict[str, Any]) -> tuple[bool, str]:
        if tool_name in self.disallowed_tools:
            return False, f"'{tool_name}' is disallowed."
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False, f"'{tool_name}' is not in the allowed list."
        if spec.get("sends_data_externally") and self.data_export_policy == "metadata_only":
            return (
                False,
                f"'{tool_name}' sends data externally; disallowed under metadata_only policy.",
            )
        return True, ""

    def needs_approval(self, tool_name: str, spec: dict[str, Any]) -> bool:
        if self.mode == ApprovalMode.ADVISORY:
            return True
        if self.mode == ApprovalMode.MANAGED:
            return spec.get("requires_write") or False
        # SEMI_AUTO: read-only + analysis auto; writes + external need approve
        if self.mode == ApprovalMode.SEMI_AUTO:
            return spec.get("requires_write", False) or spec.get("sends_data_externally", False)
        return False


class ConversationManager:
    """Stores the ongoing conversation context for the agent."""

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self.messages: list[dict[str, Any]] = []
        self.current_plan: AgentPlan | None = None
        self.history: list[dict[str, Any]] = []

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def tool_context(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "history": self.history[-10:] if self.history else [],
        }


class CheckpointManager:
    """Persists agent plan state for pause/resume/recovery."""

    def __init__(self, storage_path: str = "") -> None:
        self._path = storage_path or "data/agent_checkpoints"
        import os

        os.makedirs(self._path, exist_ok=True)

    def save(self, plan: AgentPlan) -> None:
        import os

        filepath = os.path.join(self._path, f"{plan.plan_id}.json")
        with open(filepath, "w") as f:
            json.dump(plan.to_dict(), f, indent=2, ensure_ascii=False)

    def load(self, plan_id: str) -> AgentPlan | None:
        import os

        filepath = os.path.join(self._path, f"{plan_id}.json")
        if not os.path.exists(filepath):
            return None
        with open(filepath) as f:
            data = json.load(f)
        plan = AgentPlan(plan_id=data["plan_id"], user_goal=data.get("user_goal", ""))
        plan.status = PlanStatus(data.get("status", "draft"))
        plan.steps = [_dict_to_step(s) for s in data.get("steps", [])]
        return plan


def _dict_to_step(d: dict) -> AgentStep:
    s = AgentStep(step_id=d["step_id"], tool=d["tool"], parameters=d.get("parameters", {}))
    s.status = StepStatus(d.get("status", "pending"))
    s.result = d.get("result")
    s.error = d.get("error")
    s.retry_count = d.get("retry_count", 0)
    s.approval_granted = d.get("approval_granted", False)
    return s


def create_plan(
    user_goal: str,
    tool_sequence: list[dict[str, Any]],
    approval_mode: ApprovalMode = ApprovalMode.ADVISORY,
) -> AgentPlan:
    """Build a plan from a sequence of tool calls."""
    plan = AgentPlan(
        plan_id=str(uuid.uuid4()),
        user_goal=user_goal,
        approval_mode=approval_mode,
        created_at=datetime.now(UTC).isoformat(),
    )
    for i, step_dict in enumerate(tool_sequence):
        plan.steps.append(
            AgentStep(
                step_id=f"step_{i + 1:03d}",
                tool=step_dict.get("tool", ""),
                parameters=step_dict.get("parameters", {}),
            )
        )
    plan.status = PlanStatus.APPROVED if approval_mode == ApprovalMode.MANAGED else PlanStatus.DRAFT
    return plan


class AgentRuntime:
    """Top-level agent controller — orchestrate plan → execute → checkpoint."""

    def __init__(
        self,
        conversation: ConversationManager | None = None,
        policy: PolicyEngine | None = None,
        checkpoint: CheckpointManager | None = None,
    ) -> None:
        self.conversation = conversation or ConversationManager()
        self.policy = policy or PolicyEngine()
        self.checkpoint = checkpoint or CheckpointManager()
        self._running = False
        self._paused = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        if self.conversation.current_plan:
            self.conversation.current_plan.status = PlanStatus.PAUSED
            self.checkpoint.save(self.conversation.current_plan)

    def resume_plan(self, plan_id: str) -> AgentPlan | None:
        plan = self.checkpoint.load(plan_id)
        if plan:
            self.conversation.current_plan = plan
            self._paused = False
        return plan

    def cancel(self) -> None:
        self._running = False
        if self.conversation.current_plan:
            self.conversation.current_plan.status = PlanStatus.CANCELLED
            self.checkpoint.save(self.conversation.current_plan)

    def execute_plan(self, plan: AgentPlan) -> PlanStatus:
        """Run a plan step by step, respecting policy and checkpoints."""
        self.conversation.current_plan = plan
        if plan.status in {PlanStatus.CANCELLED, PlanStatus.COMPLETED, PlanStatus.FAILED}:
            self.checkpoint.save(plan)
            return plan.status

        if not plan.steps:
            plan.error = "Plan contains no steps."
            plan.status = PlanStatus.FAILED
            self.checkpoint.save(plan)
            return plan.status

        plan.status = PlanStatus.RUNNING
        plan.created_at = datetime.now(UTC).isoformat()
        self._running = True

        from app.agent.tools import TOOL_REGISTRY

        for step in plan.steps:
            if not self._running or self._paused:
                break
            if plan.completed_count() >= plan.max_steps:
                plan.error = "Step budget exhausted."
                plan.status = PlanStatus.FAILED
                break

            spec = TOOL_REGISTRY.get(step.tool)
            if spec is None:
                step.status = StepStatus.FAILED
                step.error = f"Unknown tool: {step.tool}"
                continue

            allowed, reason = self.policy.can_use_tool(step.tool, spec.to_dict())
            if not self._running:
                break
            if not allowed:
                step.status = StepStatus.FAILED
                step.error = reason
                continue

            needs = self.policy.needs_approval(step.tool, spec.to_dict())
            if needs and not step.approval_granted:
                step.status = StepStatus.AWAITING_APPROVAL
                plan.status = PlanStatus.PAUSED
                self.checkpoint.save(plan)
                break

            step.status = StepStatus.RUNNING
            step.started_at = datetime.now(UTC).isoformat()
            # P0 deliberately has no production tool adapters. Returning a
            # failure is safer than exposing the former fixed mock results as
            # evidence of a real analysis.
            step.status = StepStatus.FAILED
            step.error = "AGENT_NOT_READY: No production adapter is available for this tool."
            step.completed_at = datetime.now(UTC).isoformat()
            plan.updated_at = datetime.now(UTC).isoformat()
            self.checkpoint.save(plan)

        self._running = False
        if plan.status in {PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.FAILED}:
            pass
        elif plan.completed_count() == len(plan.steps):
            plan.status = PlanStatus.COMPLETED
        else:
            plan.status = PlanStatus.FAILED
        self.checkpoint.save(plan)
        return plan.status
