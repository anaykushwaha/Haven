from __future__ import annotations

import pytest
from pydantic import ValidationError

from haven.agents.bedrock_reasoner import (
    BedrockReasoner,
    ModelDecisionError,
    ModelInvocationError,
    candidates_by_id,
    decision_to_reasoned_plan,
    offer_candidates,
    wrap_provider_error,
)
from haven.agents.planner import create_plan, generate_candidates
from haven.agents.policies import validate_plan_policy
from haven.models.context import HouseholdContext
from haven.models.plan import ActionRisk, PlanStatus
from haven.planning.models import ActionCandidate, ModelPlanDecision, ReasonedPlan


def _candidates(context: HouseholdContext) -> list[ActionCandidate]:
    return generate_candidates("Get movie night ready", context)


def _decision(**kwargs: object) -> ModelPlanDecision:
    return ModelPlanDecision(**{"summary": "A plan", **kwargs})


# --- Offering ----------------------------------------------------------------- #


def test_offered_candidates_get_stable_temporary_ids(movie_night_context: HouseholdContext) -> None:
    offered = offer_candidates(_candidates(movie_night_context))
    assert list(offered) == ["candidate-1", "candidate-2", "candidate-3"]
    assert offered["candidate-2"].action == "prepare_media_session"
    assert offered["candidate-2"].device_id == "tv-1"


# --- Decision validation ------------------------------------------------------ #


def test_valid_selection_builds_reasoned_plan(movie_night_context: HouseholdContext) -> None:
    candidates = _candidates(movie_night_context)
    decision = _decision(
        summary="  Queue the short feature, then dim.  ",
        selected_candidate_ids=["candidate-1", "candidate-3", "candidate-2"],
    )
    reasoned = decision_to_reasoned_plan("goal", decision, candidates_by_id(candidates))

    assert isinstance(reasoned, ReasonedPlan)
    assert reasoned.summary == "Queue the short feature, then dim."
    assert [s.action for s in reasoned.steps] == [
        "select_media",
        "dim_lights",
        "prepare_media_session",
    ]
    assert [s.order for s in reasoned.steps] == [1, 2, 3]
    assert not reasoned.clarification_needed


def test_subset_selection_is_allowed(movie_night_context: HouseholdContext) -> None:
    candidates = _candidates(movie_night_context)
    reasoned = decision_to_reasoned_plan(
        "goal",
        _decision(selected_candidate_ids=["candidate-2"]),
        candidates_by_id(candidates),
    )
    assert [s.action for s in reasoned.steps] == ["prepare_media_session"]


def test_unknown_candidate_id_rejected(movie_night_context: HouseholdContext) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    with pytest.raises(ModelDecisionError, match="unknown candidate"):
        decision_to_reasoned_plan(
            "goal", _decision(selected_candidate_ids=["candidate-1", "unlock-door"]), offered
        )


def test_duplicate_candidate_id_rejected(movie_night_context: HouseholdContext) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    with pytest.raises(ModelDecisionError, match="more than once"):
        decision_to_reasoned_plan(
            "goal", _decision(selected_candidate_ids=["candidate-1", "candidate-1"]), offered
        )


def test_empty_selection_without_clarification_rejected(
    movie_night_context: HouseholdContext,
) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    with pytest.raises(ModelDecisionError, match="no candidates"):
        decision_to_reasoned_plan("goal", _decision(selected_candidate_ids=[]), offered)


def test_clarification_without_prompt_rejected(movie_night_context: HouseholdContext) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    with pytest.raises(ModelDecisionError, match="without a question"):
        decision_to_reasoned_plan(
            "goal", _decision(clarification_needed=True, clarification_prompt="  "), offered
        )


def test_clarification_with_selection_rejected(movie_night_context: HouseholdContext) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    with pytest.raises(ModelDecisionError, match="also selected"):
        decision_to_reasoned_plan(
            "goal",
            _decision(
                clarification_needed=True,
                clarification_prompt="Which room?",
                selected_candidate_ids=["candidate-1"],
            ),
            offered,
        )


def test_valid_clarification_is_returned(movie_night_context: HouseholdContext) -> None:
    offered = candidates_by_id(_candidates(movie_night_context))
    reasoned = decision_to_reasoned_plan(
        "goal",
        _decision(clarification_needed=True, clarification_prompt="Which title do you prefer?"),
        offered,
    )
    assert reasoned.clarification_needed
    assert reasoned.steps == []
    assert reasoned.clarification_prompt == "Which title do you prefer?"


# --- Safety boundary ---------------------------------------------------------- #


def test_model_cannot_change_device_risk_or_confirmation(
    movie_night_context: HouseholdContext,
) -> None:
    # A candidate that is HIGH_IMPACT. The model only returns IDs, so it has
    # no channel through which to alter risk, device, or confirmation.
    unlock = ActionCandidate(
        action="unlock_door",
        title="Unlock front door",
        description="Unlock",
        risk=ActionRisk.HIGH_IMPACT_ACTION,
        required_capabilities=["lock"],
        required_device_id="lock-1",
    )
    candidates = [*_candidates(movie_night_context), unlock]
    offered = candidates_by_id(candidates)

    reasoned = decision_to_reasoned_plan(
        "goal",
        _decision(selected_candidate_ids=["candidate-4", "candidate-1"]),
        offered,
    )
    unlock_step = reasoned.steps[0]
    assert unlock_step.risk is ActionRisk.HIGH_IMPACT_ACTION
    assert unlock_step.requires_confirmation is True
    assert unlock_step.device_id == "lock-1"


def test_model_schema_has_no_actionable_fields() -> None:
    fields = set(ModelPlanDecision.model_fields)
    assert fields == {
        "summary",
        "selected_candidate_ids",
        "clarification_needed",
        "clarification_prompt",
    }
    with pytest.raises(ValidationError):  # extra="forbid"
        ModelPlanDecision(summary="x", selected_candidate_ids=[], device_id="lock-1")  # type: ignore[call-arg]


def test_end_to_end_with_stubbed_model_is_policy_valid(
    movie_night_context: HouseholdContext,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_invoke(system_prompt: str, prompt: str) -> ModelPlanDecision:
        calls.append((system_prompt, prompt))
        return _decision(
            summary="Prepare the short feature and dim the lamp.",
            selected_candidate_ids=["candidate-2", "candidate-3"],
        )

    reasoner = BedrockReasoner(invoke_model=fake_invoke)
    plan = create_plan("Get movie night ready", movie_night_context, reasoner=reasoner)

    assert len(calls) == 1
    system_prompt, prompt = calls[0]
    assert "Haven" in system_prompt
    assert "candidate-1" in prompt and "candidate-3" in prompt
    assert [s.action for s in plan.steps] == ["prepare_media_session", "dim_lights"]
    assert plan.status is PlanStatus.PROPOSED
    assert validate_plan_policy(plan).valid
    # Temporary candidate IDs never leak into the public plan.
    assert "candidate-" not in plan.model_dump_json()


def test_no_candidates_skips_model_and_requests_clarification(
    movie_night_context: HouseholdContext,
) -> None:
    def must_not_be_called(system_prompt: str, prompt: str) -> ModelPlanDecision:
        raise AssertionError("model should not be invoked with zero candidates")

    ctx = movie_night_context.model_copy(update={"available_minutes": 10})
    reasoner = BedrockReasoner(invoke_model=must_not_be_called)
    plan = create_plan("Get movie night ready", ctx, reasoner=reasoner)
    assert plan.clarification_needed and plan.steps == []
    assert plan.clarification_prompt and "available time" in plan.clarification_prompt


# --- Provider error handling -------------------------------------------------- #


class _FakeClientError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


class NoCredentialsError(Exception):
    pass


class LoginRefreshRequired(Exception):
    pass


class StructuredOutputException(Exception):
    pass


def test_provider_errors_are_wrapped_with_category(movie_night_context: HouseholdContext) -> None:
    def failing(system_prompt: str, prompt: str) -> ModelPlanDecision:
        raise _FakeClientError("AccessDeniedException", "Your account is still being verified.")

    reasoner = BedrockReasoner(invoke_model=failing)
    with pytest.raises(ModelInvocationError) as info:
        create_plan("Get movie night ready", movie_night_context, reasoner=reasoner)

    assert info.value.category == "access_denied"
    assert "still being verified" in str(info.value)
    assert isinstance(info.value.__cause__, _FakeClientError)


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (NoCredentialsError("no creds"), "credentials"),
        (LoginRefreshRequired("session expired"), "credentials"),
        (StructuredOutputException("bad output"), "structured_output"),
        (_FakeClientError("ThrottlingException", "slow down"), "throttled"),
        (_FakeClientError("ValidationException", "bad model id"), "validation"),
        (RuntimeError("boom"), "provider"),
    ],
)
def test_wrap_provider_error_categories(exc: Exception, category: str) -> None:
    wrapped = wrap_provider_error(exc)
    assert wrapped.category == category


class ReadTimeoutError(Exception):
    pass


class EndpointConnectionError(Exception):
    pass


class ModelThrottledException(Exception):
    pass


class ContextWindowOverflowException(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        # timeouts
        (ReadTimeoutError("read timed out"), "timeout"),
        (TimeoutError("timed out"), "timeout"),
        (_FakeClientError("ModelTimeoutException", "model timed out"), "timeout"),
        # no auth / expired session
        (_FakeClientError("ExpiredTokenException", "token expired"), "credentials"),
        (_FakeClientError("UnrecognizedClientException", "bad token"), "credentials"),
        # access denied / account restriction (message preserved separately)
        (_FakeClientError("AccessDeniedException", "not authorized to invoke"), "access_denied"),
        # model unavailable / not found
        (_FakeClientError("ServiceUnavailableException", "try later"), "unavailable"),
        (_FakeClientError("ModelNotReadyException", "warming up"), "unavailable"),
        (_FakeClientError("ResourceNotFoundException", "no such model"), "model_not_found"),
        # throttling
        (ModelThrottledException("slow down"), "throttled"),
        (_FakeClientError("ServiceQuotaExceededException", "quota"), "throttled"),
        # network / prompt
        (EndpointConnectionError("no route"), "network"),
        (ContextWindowOverflowException("too big"), "prompt_too_large"),
    ],
)
def test_failure_matrix_categories(exc: Exception, category: str) -> None:
    wrapped = wrap_provider_error(exc)
    assert wrapped.category == category
    assert str(wrapped).startswith(f"[{category}]")


def test_account_restriction_message_is_preserved_without_credentials() -> None:
    exc = _FakeClientError(
        "AccessDeniedException",
        "Your account is not authorized to invoke this model until verification completes.",
    )
    wrapped = wrap_provider_error(exc)
    assert wrapped.category == "access_denied"
    assert "verification" in str(wrapped)
    assert "AKIA" not in str(wrapped)


def test_wrapping_is_idempotent() -> None:
    original = ModelInvocationError("timeout", "x")
    assert wrap_provider_error(original) is original


@pytest.mark.parametrize("returned", [None, {"summary": "dict, not model"}, "text"])
def test_empty_or_untyped_decision_is_a_provider_failure(
    movie_night_context: HouseholdContext, returned: object
) -> None:
    reasoner = BedrockReasoner(invoke_model=lambda s, p: returned)  # type: ignore[arg-type,return-value]
    with pytest.raises(ModelInvocationError) as info:
        create_plan("Get movie night ready", movie_night_context, reasoner=reasoner)
    assert info.value.category == "structured_output"


def test_unexpected_exception_is_chained(movie_night_context: HouseholdContext) -> None:
    class Weird(Exception):
        pass

    def failing(system_prompt: str, prompt: str) -> ModelPlanDecision:
        raise Weird("something odd")

    with pytest.raises(ModelInvocationError) as info:
        create_plan(
            "Get movie night ready",
            movie_night_context,
            reasoner=BedrockReasoner(invoke_model=failing),
        )
    assert info.value.category == "provider"
    assert isinstance(info.value.__cause__, Weird)


def test_model_cannot_downgrade_risk_or_remove_confirmation(
    movie_night_context: HouseholdContext,
) -> None:
    """Leaving home offers HIGH_IMPACT candidates; the model only orders them."""

    goal = "We're leaving the house"
    candidates = generate_candidates(goal, movie_night_context)
    offered = offer_candidates(candidates)
    lock_id = next(cid for cid, c in offered.items() if c.action == "lock_door")
    decision = _decision(summary="Lock first", selected_candidate_ids=[lock_id])
    plan = create_plan(
        goal, movie_night_context, reasoner=BedrockReasoner(invoke_model=lambda s, p: decision)
    )

    assert [s.action for s in plan.steps] == ["lock_door"]
    assert plan.steps[0].risk is ActionRisk.HIGH_IMPACT_ACTION
    assert plan.steps[0].requires_confirmation is True
    assert plan.requires_confirmation and plan.status is PlanStatus.AWAITING_CONFIRMATION
    assert validate_plan_policy(plan).valid


def test_no_silent_fallback_to_mock(movie_night_context: HouseholdContext) -> None:
    def failing(system_prompt: str, prompt: str) -> ModelPlanDecision:
        raise RuntimeError("bedrock down")

    with pytest.raises(ModelInvocationError):
        create_plan(
            "Get movie night ready",
            movie_night_context,
            reasoner=BedrockReasoner(invoke_model=failing),
        )


def test_invalid_decision_propagates_as_value_error(movie_night_context: HouseholdContext) -> None:
    def invents(system_prompt: str, prompt: str) -> ModelPlanDecision:
        return _decision(selected_candidate_ids=["candidate-99"])

    with pytest.raises(ValueError, match="unknown candidate"):
        create_plan(
            "Get movie night ready",
            movie_night_context,
            reasoner=BedrockReasoner(invoke_model=invents),
        )
