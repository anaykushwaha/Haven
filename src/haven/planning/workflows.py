"""Deterministic workflow classification for household goals.

The planner needs to know *which kind* of request it is handling before it
can enumerate candidate actions. That decision is made here with a small,
ordered keyword table rather than an LLM call, so it is cheap, testable and
identical across the mock and Bedrock backends. The model later chooses
*among* the candidates a workflow produced; it never chooses the workflow.

Adding a workflow means adding one enum member and one keyword row.
"""

from __future__ import annotations

import re
from enum import StrEnum


class WorkflowType(StrEnum):
    """The household workflows Haven knows how to plan."""

    MOVIE_NIGHT = "movie_night"
    LEAVING_HOME = "leaving_home"
    TONIGHT_PLANNING = "tonight_planning"
    UNKNOWN = "unknown"


# Ordered: the first workflow whose pattern matches wins. Specific intents
# come before the open-ended "what should we do" request, and leaving the
# house beats a stray media word ("leaving for the movie theater" is a
# departure, not a movie night).
_WORKFLOW_PATTERNS: tuple[tuple[WorkflowType, tuple[str, ...]], ...] = (
    (
        WorkflowType.LEAVING_HOME,
        (
            r"\bleav(?:e|ing)\b",
            r"\bhead(?:ing)?\s+out\b",
            r"\bgoing\s+out\b",
            r"\bout\s+of\s+the\s+house\b",
            r"\block\s+(?:up|the\s+house|the\s+door|everything)\b",
            r"\bsecure\s+the\s+(?:house|home)\b",
            r"\baway\s+mode\b",
            r"\bdeparture\b",
        ),
    ),
    (
        WorkflowType.MOVIE_NIGHT,
        (
            r"\bmovies?\b",
            r"\bfilms?\b",
            r"\bcinema\b",
            r"\bmovie\s+night\b",
            r"\bwatch\s+(?:a\s+|some)?(?:thing|show|series|episode)\b",
        ),
    ),
    (
        WorkflowType.TONIGHT_PLANNING,
        (
            r"\btonight\b",
            r"\bthis\s+evening\b",
            r"\bwhat\s+should\s+we\s+do\b",
            r"\bwhat\s+(?:can|could)\s+we\s+do\b",
            r"\bany\s+(?:ideas|suggestions)\b",
            r"\bsomething\s+to\s+do\b",
        ),
    ),
)

_COMPILED: tuple[tuple[WorkflowType, tuple[re.Pattern[str], ...]], ...] = tuple(
    (workflow, tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns))
    for workflow, patterns in _WORKFLOW_PATTERNS
)


def classify_workflow(goal: str) -> WorkflowType:
    """Return the workflow a goal belongs to, or ``UNKNOWN``.

    Pure and deterministic: the same text always yields the same workflow.
    """

    text = " ".join((goal or "").split())
    if not text:
        return WorkflowType.UNKNOWN
    for workflow, patterns in _COMPILED:
        if any(pattern.search(text) for pattern in patterns):
            return workflow
    return WorkflowType.UNKNOWN


def is_supported_workflow(workflow: WorkflowType) -> bool:
    return workflow is not WorkflowType.UNKNOWN


__all__ = ["WorkflowType", "classify_workflow", "is_supported_workflow"]
