from __future__ import annotations

import pytest

from haven.planning.workflows import WorkflowType, classify_workflow, is_supported_workflow


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("Get movie night ready", WorkflowType.MOVIE_NIGHT),
        ("Set up a FILM for us", WorkflowType.MOVIE_NIGHT),
        ("Let's watch something", WorkflowType.MOVIE_NIGHT),
        ("Movie tonight please", WorkflowType.MOVIE_NIGHT),
        ("We're leaving the house", WorkflowType.LEAVING_HOME),
        ("Heading out for dinner, lock up", WorkflowType.LEAVING_HOME),
        ("Secure the house, we are going out", WorkflowType.LEAVING_HOME),
        ("Leaving for the movie theater", WorkflowType.LEAVING_HOME),
        ("Put the house in away mode", WorkflowType.LEAVING_HOME),
        ("What should we do tonight?", WorkflowType.TONIGHT_PLANNING),
        ("Any ideas for this evening?", WorkflowType.TONIGHT_PLANNING),
        ("Launch the rocket", WorkflowType.UNKNOWN),
        ("", WorkflowType.UNKNOWN),
        ("   ", WorkflowType.UNKNOWN),
    ],
)
def test_classify_workflow(goal: str, expected: WorkflowType) -> None:
    assert classify_workflow(goal) is expected


def test_classification_is_deterministic_and_whitespace_insensitive() -> None:
    assert classify_workflow("  get   movie\nnight ready ") is WorkflowType.MOVIE_NIGHT
    assert classify_workflow("get movie night ready") is classify_workflow("GET MOVIE NIGHT READY")


def test_word_boundaries_avoid_false_positives() -> None:
    # "filming" is not "film"; "believing" is not "leaving".
    assert classify_workflow("Start filming the believing crowd") is WorkflowType.UNKNOWN


def test_supported_workflows() -> None:
    assert is_supported_workflow(WorkflowType.MOVIE_NIGHT)
    assert is_supported_workflow(WorkflowType.LEAVING_HOME)
    assert is_supported_workflow(WorkflowType.TONIGHT_PLANNING)
    assert not is_supported_workflow(WorkflowType.UNKNOWN)
    assert {w.value for w in WorkflowType} == {
        "movie_night",
        "leaving_home",
        "tonight_planning",
        "unknown",
    }
