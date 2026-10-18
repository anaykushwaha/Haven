"""Execution result contracts.

`Plan` / `PlanStatus` (owned by Person A, in `haven.models.plan`) only
carry a *proposal* lifecycle (`PROPOSED`, `AWAITING_CONFIRMATION`); by
design, execution states are not part of that contract yet. These types
are execution's own boundary: what the executor hands to the verifier,
and what the verifier and MCP layer read back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StepExecutionStatus(StrEnum):
    """Outcome of executing a single plan step."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StepExecutionResult(BaseModel):
    """What happened when one `PlanStep` was executed."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: str
    status: StepExecutionStatus
    detail: str
    effect: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    error: str | None = None
    attempts: int = Field(default=1, ge=1)
    """How many times the step was run (more than 1 after a retry/recovery)."""


class ExecutionStatus(StrEnum):
    """Overall outcome of executing a plan."""

    COMPLETED = "COMPLETED"
    """Every step succeeded (or was intentionally a no-op read)."""

    PARTIAL = "PARTIAL"
    """At least one step succeeded and at least one failed or was skipped."""

    FAILED = "FAILED"
    """No step succeeded."""

    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    """The plan requires confirmation and none was supplied; nothing ran."""

    NO_ACTION = "NO_ACTION"
    """The plan had no steps to run (e.g. it only asks a clarifying question)."""


class ExecutionResult(BaseModel):
    """The result of executing a `Plan`."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    status: ExecutionStatus
    step_results: list[StepExecutionResult] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @property
    def succeeded_step_ids(self) -> list[str]:
        return [
            result.step_id
            for result in self.step_results
            if result.status is StepExecutionStatus.SUCCEEDED
        ]

    @property
    def failed_step_ids(self) -> list[str]:
        return [
            result.step_id
            for result in self.step_results
            if result.status is StepExecutionStatus.FAILED
        ]


def status_for_results(step_results: list[StepExecutionResult]) -> ExecutionStatus:
    """Derive an overall `ExecutionStatus` from individual step results."""

    if not step_results:
        return ExecutionStatus.FAILED

    succeeded = any(r.status is StepExecutionStatus.SUCCEEDED for r in step_results)
    failed_or_skipped = any(r.status is not StepExecutionStatus.SUCCEEDED for r in step_results)

    if succeeded and not failed_or_skipped:
        return ExecutionStatus.COMPLETED
    if succeeded and failed_or_skipped:
        return ExecutionStatus.PARTIAL
    return ExecutionStatus.FAILED


__all__ = [
    "StepExecutionStatus",
    "StepExecutionResult",
    "ExecutionStatus",
    "ExecutionResult",
    "status_for_results",
]
