"""DynamoDB adapters: idempotent plan writes and explicit failure handling (offline)."""

from __future__ import annotations

import pytest

from aws.dynamodb.config import DynamoDBConfig
from aws.dynamodb.errors import wrap_dynamodb_error
from aws.dynamodb.repositories import (
    DynamoDBActivityStore,
    DynamoDBPlanRepository,
    DynamoDBPreferenceStore,
    create_dynamodb_repositories,
)
from haven.agents.planner import create_plan
from haven.memory.activity import ActivityType
from haven.memory.errors import PersistenceError, PlanConflictError
from haven.memory.preferences import PreferenceKey
from haven.memory.service import MemoryService
from haven.models.context import HouseholdContext
from haven.models.plan import Plan
from tests.fakes.dynamodb import FakeDynamoDBResource, FakeTable, client_error, haven_tables

HH = "household-ddb"


@pytest.fixture
def tables() -> tuple[FakeTable, FakeTable, FakeTable]:
    return haven_tables()


@pytest.fixture
def plans(tables: tuple[FakeTable, FakeTable, FakeTable]) -> DynamoDBPlanRepository:
    return DynamoDBPlanRepository(tables[2])


@pytest.fixture
def plan(movie_night_context: HouseholdContext) -> Plan:
    return create_plan("Get movie night ready", movie_night_context)


# --- Plans: conditional, idempotent writes ----------------------------------- #


def test_first_save_is_conditional(plans: DynamoDBPlanRepository, plan: Plan, tables) -> None:
    plans.save_plan(plan, HH)
    op, kwargs = tables[2].calls[0]
    assert op == "put_item"
    assert kwargs["ConditionExpression"] == "attribute_not_exists(plan_id)"
    assert plans.get_plan(plan.id) == plan


def test_duplicate_identical_save_is_idempotent(
    plans: DynamoDBPlanRepository, plan: Plan, tables
) -> None:
    plans.save_plan(plan, HH)
    plans.save_plan(plan, HH)  # replayed write, e.g. a retried request
    plans.save_plan(Plan.model_validate_json(plan.model_dump_json()), HH)

    assert len(tables[2].items) == 1
    assert plans.get_plan(plan.id) == plan
    # Only the first put landed; replays were detected by the condition + read.
    assert tables[2].ops().count("put_item") == 3
    assert tables[2].ops().count("get_item") >= 2


def test_conflicting_duplicate_is_rejected_and_original_kept(
    plans: DynamoDBPlanRepository, plan: Plan
) -> None:
    plans.save_plan(plan, HH)
    changed = plan.model_copy(update={"summary": "something else"})

    with pytest.raises(PlanConflictError) as info:
        plans.save_plan(changed, HH)
    assert info.value.category == "conflict"
    assert info.value.plan_id == plan.id
    assert isinstance(info.value, PersistenceError)
    assert plans.get_plan(plan.id).summary == plan.summary  # durable state untouched


def test_explicit_overwrite_replaces(plans: DynamoDBPlanRepository, plan: Plan, tables) -> None:
    plans.save_plan(plan, HH)
    changed = plan.model_copy(update={"summary": "updated by owner"})
    plans.save_plan(changed, HH, overwrite=True)

    assert plans.get_plan(plan.id).summary == "updated by owner"
    _, kwargs = tables[2].calls[-1]
    assert "ConditionExpression" not in kwargs


def test_missing_plan_returns_none_and_delete_is_honest(plans: DynamoDBPlanRepository) -> None:
    assert plans.get_plan("plan-missing") is None
    assert plans.delete_plan("plan-missing") is False


def test_delete_then_resave_works(plans: DynamoDBPlanRepository, plan: Plan) -> None:
    plans.save_plan(plan, HH)
    assert plans.delete_plan(plan.id) is True
    plans.save_plan(plan, HH)
    assert plans.get_plan(plan.id) == plan


def test_corrupted_plan_payload_is_reported_not_echoed(
    plans: DynamoDBPlanRepository, tables
) -> None:
    tables[2].items[("plan-bad",)] = {"plan_id": "plan-bad", "payload": '{"not": "a plan"'}
    with pytest.raises(PersistenceError) as info:
        plans.get_plan("plan-bad")
    assert info.value.category == "corrupt"
    assert "not" not in str(info.value).replace("could not", "")
    assert "plan-bad" in str(info.value)


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_ids_are_rejected_before_any_io(plans: DynamoDBPlanRepository, plan: Plan, bad):
    with pytest.raises(ValueError):
        plans.save_plan(plan, bad)
    with pytest.raises(ValueError):
        plans.get_plan(bad)


# --- Failure categories -------------------------------------------------------- #


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("ResourceNotFoundException", "table_unavailable"),
        ("AccessDeniedException", "access_denied"),
        ("ProvisionedThroughputExceededException", "throttled"),
        ("ThrottlingException", "throttled"),
        ("InternalServerError", "unavailable"),
        ("RequestTimeout", "timeout"),
        ("ValidationException", "validation"),
        ("ExpiredTokenException", "credentials"),
    ],
)
def test_write_failures_surface_with_category(
    plans: DynamoDBPlanRepository, plan: Plan, tables, code: str, category: str
) -> None:
    tables[2].fail("put_item", client_error(code))
    with pytest.raises(PersistenceError) as info:
        plans.save_plan(plan, HH)
    assert info.value.category == category
    assert info.value.operation == "save_plan"
    assert info.value.__cause__ is not None
    assert not tables[2].items  # nothing half-written


def test_read_failure_surfaces(plans: DynamoDBPlanRepository, tables) -> None:
    tables[2].fail("get_item", client_error("AccessDeniedException", "GetItem"))
    with pytest.raises(PersistenceError) as info:
        plans.get_plan("plan-1")
    assert info.value.category == "access_denied"


class NoCredentialsError(Exception):
    """Mirrors botocore.exceptions.NoCredentialsError by name, not import."""


class ReadTimeoutError(Exception):
    """Mirrors botocore.exceptions.ReadTimeoutError by name, not import."""


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (NoCredentialsError("none"), "credentials"),
        (ReadTimeoutError("slow"), "timeout"),
        (TimeoutError("slow"), "timeout"),
        (ConnectionError("down"), "network"),
        (client_error("ConditionalCheckFailedException"), "conflict"),
        (RuntimeError("???"), "provider"),
    ],
)
def test_wrap_dynamodb_error_categories(exc: Exception, category: str) -> None:
    wrapped = wrap_dynamodb_error(exc, operation="op")
    assert wrapped.category == category
    assert wrapped.operation == "op"
    assert "op" in str(wrapped)


def test_persistence_error_message_has_no_payload(plans: DynamoDBPlanRepository, plan, tables):
    tables[2].fail("put_item", client_error("AccessDeniedException"))
    with pytest.raises(PersistenceError) as info:
        plans.save_plan(plan, HH)
    assert "Short Feature" not in str(info.value)
    assert plan.id not in str(info.value)


# --- Preferences / activity through MemoryService ---------------------------- #


def _service(tables) -> MemoryService:
    repos = create_dynamodb_repositories(
        DynamoDBConfig(
            region="us-west-2",
            preferences_table=tables[0].name,
            activities_table=tables[1].name,
            plans_table=tables[2].name,
        ),
        dynamodb=FakeDynamoDBResource(tables),
    )
    assert isinstance(repos.preferences, DynamoDBPreferenceStore)
    assert isinstance(repos.activity, DynamoDBActivityStore)
    return MemoryService(preferences=repos.preferences, activity=repos.activity)


def test_preference_write_failure_is_not_hidden(tables) -> None:
    memory = _service(tables)
    tables[0].fail("put_item", client_error("ProvisionedThroughputExceededException"))
    with pytest.raises(PersistenceError) as info:
        memory.save_preference(HH, PreferenceKey.PREFERRED_ROOM, "living-room")
    assert info.value.category == "throttled"
    assert not tables[0].items and not tables[1].items


def test_audit_failure_after_durable_write_is_logged_not_fatal(tables, caplog) -> None:
    memory = _service(tables)
    tables[1].fail("put_item", client_error("AccessDeniedException"))
    with caplog.at_level("WARNING"):
        saved = memory.save_preference(HH, PreferenceKey.PREFERRED_ROOM, "living-room")
    assert saved.value == "living-room"
    assert memory.get_preference(HH, PreferenceKey.PREFERRED_ROOM).value == "living-room"
    assert any("Audit record" in r.message and "access_denied" in r.message for r in caplog.records)
    assert "living-room" not in caplog.text


def test_activity_record_failure_surfaces(tables) -> None:
    memory = _service(tables)
    tables[1].fail("put_item", client_error("ResourceNotFoundException"))
    with pytest.raises(PersistenceError) as info:
        memory.record_activity(HH, ActivityType.PLAN_PROPOSED, "x")
    assert info.value.category == "table_unavailable"


def test_activity_query_failure_surfaces(tables) -> None:
    memory = _service(tables)
    tables[1].fail("query", client_error("ThrottlingException", "Query"))
    with pytest.raises(PersistenceError) as info:
        memory.list_activity(HH)
    assert info.value.category == "throttled"


def test_corrupted_preference_row_is_reported(tables) -> None:
    memory = _service(tables)
    tables[0].items[(HH, "HOUSEHOLD#preferred_room")] = {
        "household_id": HH,
        "preference_id": "HOUSEHOLD#preferred_room",
        "key": "preferred_room",
        "value_json": "{bad json",
        "updated_at": "nope",
    }
    with pytest.raises(PersistenceError) as info:
        memory.get_preference(HH, "preferred_room")
    assert info.value.category == "corrupt"


def test_activity_pagination_and_type_filter(tables) -> None:
    memory = _service(tables)
    for i in range(5):
        memory.record_activity(HH, ActivityType.PLAN_PROPOSED, f"plan {i}")
    memory.record_activity(HH, ActivityType.MOVIE_NIGHT_PREPARED, "movie")

    assert [r.summary for r in memory.list_activity(HH, limit=2)] == ["movie", "plan 4"]
    proposed = memory.list_activity(HH, activity_type=ActivityType.PLAN_PROPOSED, limit=3)
    assert [r.summary for r in proposed] == ["plan 4", "plan 3", "plan 2"]
