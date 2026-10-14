"""Strands + Amazon Bedrock reasoning backend for the Haven planner.

    accepted ActionCandidates
        -> temporary candidate IDs (candidate-1, candidate-2, ...)
        -> prompt with ONLY those candidates + minimal context
        -> Strands Agent(structured_output_model=ModelPlanDecision) -> Bedrock
        -> deterministic validation of the decision
        -> trusted PlanSteps rebuilt from the original candidates
        -> ReasonedPlan

Why this module exists: ``aws/bedrock`` is infrastructure (how Haven connects
to Bedrock). This adapter is the planning side (how Haven reasons about a
goal). It is imported only when ``HAVEN_PLANNER_BACKEND=bedrock``; Strands
and boto3 are imported lazily inside the invoker so mock mode stays offline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from haven.agents.planner import candidate_to_step, clarification_prompt_for
from haven.agents.prompts import PLANNER_SYSTEM_PROMPT, build_planning_prompt
from haven.models.context import HouseholdContext
from haven.planning.models import (
    ActionCandidate,
    ModelPlanDecision,
    OfferedCandidate,
    ReasonedPlan,
)

if TYPE_CHECKING:
    from aws.bedrock.config import BedrockConfig

logger = logging.getLogger(__name__)

ModelInvoker = Callable[[str, str], ModelPlanDecision]
"""(system_prompt, user_prompt) -> structured decision. Injectable for tests."""

CANDIDATE_ID_PREFIX = "candidate-"


class ModelInvocationError(RuntimeError):
    """The model provider could not produce a structured decision.

    Raised for credential, access, throttling, timeout, availability and
    structured-output failures. ``category`` is a short machine-readable tag;
    the original exception is preserved as ``__cause__``.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(f"[{category}] {message}")
        self.category = category


class ModelDecisionError(ValueError):
    """The model returned a schema-valid decision that Haven rejects."""


class BedrockReasoner:
    """Reasoner backed by a Strands Agent on Amazon Bedrock.

    Satisfies the planner's ``Reasoner`` signature. Pass ``invoke_model`` to
    substitute the model call in tests; the default builds a Strands Agent
    from ``aws.bedrock`` configuration on first use.
    """

    __name__ = "bedrock_reasoner"

    def __init__(
        self,
        *,
        invoke_model: ModelInvoker | None = None,
        config: BedrockConfig | None = None,
    ) -> None:
        self._invoke_model = invoke_model
        self._config = config

    def __call__(
        self,
        goal: str,
        context: HouseholdContext,
        candidates: Sequence[ActionCandidate],
    ) -> ReasonedPlan:
        if not candidates:
            # Nothing to choose among: clarification is deterministic and no
            # model call is needed.
            return ReasonedPlan(
                summary="",
                steps=[],
                clarification_needed=True,
                clarification_prompt=clarification_prompt_for(goal, context),
            )

        offered = offer_candidates(candidates)
        prompt = build_planning_prompt(goal, context, candidates=list(offered.values()))

        invoke = self._invoke_model or self._default_invoker()
        try:
            decision = invoke(PLANNER_SYSTEM_PROMPT, prompt)
        except ModelInvocationError:
            raise
        except Exception as exc:
            error = wrap_provider_error(exc)
            logger.warning("Bedrock reasoning failed: category=%s", error.category)
            raise error from exc

        if not isinstance(decision, ModelPlanDecision):
            # An invoker that returns nothing (or the wrong type) is a provider
            # failure, not a planning decision; it must never become a plan.
            raise ModelInvocationError("structured_output", "model returned no decision")

        return decision_to_reasoned_plan(goal, decision, candidates_by_id(candidates))

    def _default_invoker(self) -> ModelInvoker:
        config = self._config
        if config is None:
            from aws.bedrock.config import load_bedrock_config

            config = load_bedrock_config()

        def invoke(system_prompt: str, prompt: str) -> ModelPlanDecision:
            return invoke_strands_structured(system_prompt, prompt, config)

        return invoke


# --------------------------------------------------------------------------- #
# Candidate offering
# --------------------------------------------------------------------------- #


def candidate_ids(candidates: Sequence[ActionCandidate]) -> list[str]:
    return [f"{CANDIDATE_ID_PREFIX}{index}" for index in range(1, len(candidates) + 1)]


def candidates_by_id(candidates: Sequence[ActionCandidate]) -> dict[str, ActionCandidate]:
    return dict(zip(candidate_ids(candidates), candidates, strict=True))


def offer_candidates(candidates: Sequence[ActionCandidate]) -> dict[str, OfferedCandidate]:
    """Assign temporary IDs and produce the model-facing view of each candidate."""

    return {
        candidate_id: OfferedCandidate.from_candidate(candidate_id, candidate)
        for candidate_id, candidate in candidates_by_id(candidates).items()
    }


# --------------------------------------------------------------------------- #
# Decision validation
# --------------------------------------------------------------------------- #


def decision_to_reasoned_plan(
    goal: str,
    decision: ModelPlanDecision,
    offered: Mapping[str, ActionCandidate],
) -> ReasonedPlan:
    """Validate a model decision and rebuild trusted steps from candidates.

    Raises `ModelDecisionError` for unknown or duplicate IDs, an empty
    selection without clarification, or clarification without a usable
    prompt. Steps come from the original `ActionCandidate` objects, so the
    model cannot alter devices, rooms, risk, or confirmation.
    """

    if decision.clarification_needed:
        prompt = (decision.clarification_prompt or "").strip()
        if not prompt:
            raise ModelDecisionError("model requested clarification without a question")
        if decision.selected_candidate_ids:
            raise ModelDecisionError("model requested clarification but also selected candidates")
        return ReasonedPlan(
            summary=decision.summary.strip(),
            steps=[],
            clarification_needed=True,
            clarification_prompt=prompt,
        )

    selected = [candidate_id.strip() for candidate_id in decision.selected_candidate_ids]
    if not selected:
        raise ModelDecisionError("model selected no candidates and did not request clarification")

    unknown = [candidate_id for candidate_id in selected if candidate_id not in offered]
    if unknown:
        raise ModelDecisionError(f"model selected unknown candidate id(s): {', '.join(unknown)}")

    if len(set(selected)) != len(selected):
        raise ModelDecisionError("model selected the same candidate more than once")

    steps = [
        candidate_to_step(offered[candidate_id], order)
        for order, candidate_id in enumerate(selected, start=1)
    ]
    summary = decision.summary.strip() or f"Proposed {len(steps)} step(s) for: {goal}"
    logger.info("Model selected %s of %s offered candidate(s)", len(steps), len(offered))
    return ReasonedPlan(summary=summary, steps=steps)


# --------------------------------------------------------------------------- #
# Provider boundary
# --------------------------------------------------------------------------- #


def invoke_strands_structured(
    system_prompt: str,
    prompt: str,
    config: BedrockConfig,
) -> ModelPlanDecision:
    """Invoke a Strands Agent on Bedrock and return its structured decision.

    Uses Strands' Pydantic structured output; no free-form JSON is parsed.
    Strands and the Bedrock client are imported here, not at module import.
    """

    from strands import Agent

    from aws.bedrock.client import create_bedrock_model

    model = create_bedrock_model(config)
    # callback_handler=None disables Strands' default stdout streaming.
    agent = Agent(model=model, system_prompt=system_prompt, callback_handler=None)
    result = agent(prompt, structured_output_model=ModelPlanDecision)

    decision = result.structured_output
    if not isinstance(decision, ModelPlanDecision):
        raise ModelInvocationError(
            "structured_output", "model response did not contain a ModelPlanDecision"
        )
    return decision


_ERROR_CODE_CATEGORIES: dict[str, str] = {
    "AccessDeniedException": "access_denied",
    "UnrecognizedClientException": "credentials",
    "InvalidSignatureException": "credentials",
    "ExpiredTokenException": "credentials",
    "ThrottlingException": "throttled",
    "TooManyRequestsException": "throttled",
    "ServiceQuotaExceededException": "throttled",
    "ServiceUnavailableException": "unavailable",
    "InternalServerException": "unavailable",
    "ModelNotReadyException": "unavailable",
    "ModelErrorException": "provider",
    "ModelStreamErrorException": "provider",
    "ResourceNotFoundException": "model_not_found",
    "ValidationException": "validation",
    "ModelTimeoutException": "timeout",
    "RequestTimeout": "timeout",
    "RequestTimeoutException": "timeout",
    "ExpiredToken": "credentials",
    "InvalidClientTokenId": "credentials",
}

_EXCEPTION_NAME_CATEGORIES: dict[str, str] = {
    "NoCredentialsError": "credentials",
    "PartialCredentialsError": "credentials",
    "CredentialRetrievalError": "credentials",
    "ProfileNotFound": "credentials",
    "TokenRetrievalError": "credentials",
    "SSOTokenLoadError": "credentials",
    "UnauthorizedSSOTokenError": "credentials",
    "LoginRefreshRequired": "credentials",
    "NoRegionError": "configuration",
    "EndpointConnectionError": "network",
    "ConnectionClosedError": "network",
    "ConnectionError": "network",
    "ConnectTimeoutError": "timeout",
    "ReadTimeoutError": "timeout",
    "TimeoutError": "timeout",
    "ModelThrottledException": "throttled",
    "StructuredOutputException": "structured_output",
    "MaxTokensReachedException": "structured_output",
    "ContextWindowOverflowException": "prompt_too_large",
    "ValidationError": "structured_output",
}


def wrap_provider_error(exc: Exception) -> ModelInvocationError:
    """Classify a provider/Strands failure without importing boto3/Strands.

    Classification is duck-typed on the exception's class name and, for
    botocore ``ClientError``, on ``response["Error"]["Code"]``. The AWS
    message is kept (it carries e.g. the account-verification notice); no
    credentials are included.
    """

    if isinstance(exc, ModelInvocationError):
        return exc

    name = type(exc).__name__
    response = getattr(exc, "response", None)
    code = ""
    aws_message = ""
    if isinstance(response, Mapping):
        error = response.get("Error") or {}
        if isinstance(error, Mapping):
            code = str(error.get("Code") or "")
            aws_message = str(error.get("Message") or "")

    category = _ERROR_CODE_CATEGORIES.get(code) or _EXCEPTION_NAME_CATEGORIES.get(name, "provider")
    detail = aws_message or str(exc) or name
    return ModelInvocationError(category, f"{code or name}: {detail}")
