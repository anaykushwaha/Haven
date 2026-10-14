"""DynamoDB-backed Haven persistence adapters.

Failure semantics (shared by every adapter here):

- Provider/botocore exceptions are translated into
  `haven.memory.errors.PersistenceError` with a category (``access_denied``,
  ``table_unavailable``, ``throttled``, ``timeout`` ...) and the original
  exception chained as ``__cause__``. Nothing is swallowed and nothing falls
  back to in-memory storage: a failed write is reported, never faked.
- Rows that cannot be decoded raise ``PersistenceError("corrupt")`` without
  echoing the payload.
- `DynamoDBPlanRepository.save_plan` is idempotent: it uses a conditional
  write (``attribute_not_exists(plan_id)``). Re-saving an identical plan is a
  no-op; saving a *different* plan under an existing id raises
  `PlanConflictError` unless ``overwrite=True`` is passed explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key
from pydantic import ValidationError

from aws.dynamodb.client import create_dynamodb_resource
from aws.dynamodb.config import DynamoDBConfig, load_dynamodb_config
from aws.dynamodb.errors import dynamodb_errors, error_code, wrap_dynamodb_error
from haven.memory.activity import ActivityRecord, ActivityStore, ActivityType
from haven.memory.errors import PersistenceError, PlanConflictError
from haven.memory.preferences import Preference, PreferenceStore
from haven.models.plan import Plan

_HOUSEHOLD_SCOPE = "HOUSEHOLD"
_PERSON_SCOPE = "PERSON"
_PLAN_ID_ABSENT = "attribute_not_exists(plan_id)"


class DynamoDBPreferenceStore(PreferenceStore):
    """DynamoDB implementation of Haven's preference store."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def save(self, preference: Preference) -> Preference:
        item = _preference_to_item(preference)
        with dynamodb_errors("save_preference"):
            self._table.put_item(Item=item)
        return preference

    def get(self, household_id: str, key: str, person_id: str | None = None) -> Preference | None:
        household_id = _require_text(household_id, "household_id")
        key = _require_text(key, "key")
        with dynamodb_errors("get_preference"):
            response = self._table.get_item(
                Key={"household_id": household_id, "preference_id": _preference_id(key, person_id)},
                ConsistentRead=True,
            )
        item = response.get("Item")
        if not item:
            return None
        return _preference_from_item(item)

    def list(self, household_id: str, person_id: str | None = None) -> list[Preference]:
        household_id = _require_text(household_id, "household_id")
        prefix = _preference_prefix(person_id)
        with dynamodb_errors("list_preferences"):
            items = _query_all(
                self._table,
                KeyConditionExpression=(
                    Key("household_id").eq(household_id) & Key("preference_id").begins_with(prefix)
                ),
                ConsistentRead=True,
            )
        preferences = [_preference_from_item(item) for item in items]
        return sorted(preferences, key=lambda preference: preference.key)

    def delete(self, household_id: str, key: str, person_id: str | None = None) -> bool:
        household_id = _require_text(household_id, "household_id")
        key = _require_text(key, "key")
        with dynamodb_errors("delete_preference"):
            response = self._table.delete_item(
                Key={"household_id": household_id, "preference_id": _preference_id(key, person_id)},
                ReturnValues="ALL_OLD",
            )
        return bool(response.get("Attributes"))


class DynamoDBActivityStore(ActivityStore):
    """DynamoDB implementation of Haven's activity history."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def record(self, activity: ActivityRecord) -> ActivityRecord:
        item = _activity_to_item(activity)
        with dynamodb_errors("record_activity"):
            self._table.put_item(Item=item)
        return activity

    def list(
        self,
        household_id: str,
        *,
        activity_type: ActivityType | None = None,
        limit: int | None = None,
    ) -> list[ActivityRecord]:
        household_id = _require_text(household_id, "household_id")
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")
        if limit == 0:
            return []

        records: list[ActivityRecord] = []
        exclusive_start_key: dict[str, Any] | None = None

        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("household_id").eq(household_id),
                "ScanIndexForward": False,
                "ConsistentRead": True,
            }
            if exclusive_start_key:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            if limit is not None and activity_type is None:
                kwargs["Limit"] = max(1, limit - len(records))

            with dynamodb_errors("list_activity"):
                response = self._table.query(**kwargs)

            for item in response.get("Items", []):
                record = _activity_from_item(item)
                if activity_type is None or record.activity_type == activity_type:
                    records.append(record)
                    if limit is not None and len(records) >= limit:
                        return records

            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break

        return records


class DynamoDBPlanRepository:
    """Persistence adapter for Haven Plan objects."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def save_plan(self, plan: Plan, household_id: str, *, overwrite: bool = False) -> Plan:
        """Persist a plan exactly once.

        - First save: conditional ``put_item`` (``attribute_not_exists``).
        - Same plan again (identical payload): no write, returns the plan.
        - Different plan with the same id: `PlanConflictError`, unless
          ``overwrite=True`` (an intentional replacement, e.g. a status update
          by the owner of the plan).
        """

        household_id = _require_text(household_id, "household_id")
        item = _plan_to_item(plan, household_id)

        if overwrite:
            with dynamodb_errors("save_plan"):
                self._table.put_item(Item=item)
            return plan

        try:
            self._table.put_item(Item=item, ConditionExpression=_PLAN_ID_ABSENT)
        except Exception as exc:
            if error_code(exc) != "ConditionalCheckFailedException":
                raise wrap_dynamodb_error(exc, operation="save_plan") from exc
            existing = self._get_item(plan.id)
            if existing is not None and existing.get("payload") == item["payload"]:
                return plan  # idempotent replay of the same write
            raise PlanConflictError(plan.id) from exc
        return plan

    def get_plan(self, plan_id: str) -> Plan | None:
        plan_id = _require_text(plan_id, "plan_id")
        item = self._get_item(plan_id)
        if item is None:
            return None
        return _plan_from_item(item)

    def delete_plan(self, plan_id: str) -> bool:
        plan_id = _require_text(plan_id, "plan_id")
        with dynamodb_errors("delete_plan"):
            response = self._table.delete_item(Key={"plan_id": plan_id}, ReturnValues="ALL_OLD")
        return bool(response.get("Attributes"))

    def _get_item(self, plan_id: str) -> dict[str, Any] | None:
        with dynamodb_errors("get_plan"):
            response = self._table.get_item(Key={"plan_id": plan_id}, ConsistentRead=True)
        item = response.get("Item")
        return dict(item) if item else None


class DynamoDBRepositories:
    """Bundle containing Haven's DynamoDB repositories."""

    def __init__(
        self,
        preferences: DynamoDBPreferenceStore,
        activity: DynamoDBActivityStore,
        plans: DynamoDBPlanRepository,
    ) -> None:
        self.preferences = preferences
        self.activity = activity
        self.plans = plans


def create_dynamodb_repositories(
    config: DynamoDBConfig | None = None,
    *,
    dynamodb: Any | None = None,
) -> DynamoDBRepositories:
    """Construct repository adapters without creating tables."""

    resolved = config or load_dynamodb_config()
    resource = dynamodb or create_dynamodb_resource(resolved)
    return DynamoDBRepositories(
        preferences=DynamoDBPreferenceStore(resource.Table(resolved.preferences_table)),
        activity=DynamoDBActivityStore(resource.Table(resolved.activities_table)),
        plans=DynamoDBPlanRepository(resource.Table(resolved.plans_table)),
    )


# --------------------------------------------------------------------------- #
# Item mapping
# --------------------------------------------------------------------------- #


def _preference_id(key: str, person_id: str | None) -> str:
    key = _require_text(key, "key")
    if person_id is None:
        return f"{_HOUSEHOLD_SCOPE}#{key}"
    return f"{_PERSON_SCOPE}#{_require_text(person_id, 'person_id')}#{key}"


def _preference_prefix(person_id: str | None) -> str:
    if person_id is None:
        return f"{_HOUSEHOLD_SCOPE}#"
    return f"{_PERSON_SCOPE}#{_require_text(person_id, 'person_id')}#"


def _preference_to_item(preference: Preference) -> dict[str, Any]:
    return {
        "household_id": preference.household_id,
        "preference_id": _preference_id(preference.key, preference.person_id),
        "key": preference.key,
        "person_id": preference.person_id or "",
        "value_json": _dumps(preference.value),
        "updated_at": _utc_iso(preference.updated_at),
    }


def _preference_from_item(item: dict[str, Any]) -> Preference:
    try:
        return Preference.model_validate(
            {
                "household_id": item["household_id"],
                "key": item["key"],
                "value": json.loads(item["value_json"]),
                "person_id": item.get("person_id") or None,
                "updated_at": item["updated_at"],
            }
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _corrupt("preference", item.get("preference_id")) from exc


def _activity_to_item(activity: ActivityRecord) -> dict[str, Any]:
    occurred_at = _utc_iso(activity.occurred_at)
    return {
        "household_id": activity.household_id,
        "activity_key": f"{occurred_at}#{activity.id}",
        "activity_id": activity.id,
        "activity_type": activity.activity_type.value,
        "summary": activity.summary,
        "details_json": _dumps(activity.details),
        "occurred_at": occurred_at,
    }


def _activity_from_item(item: dict[str, Any]) -> ActivityRecord:
    try:
        return ActivityRecord.model_validate(
            {
                "id": item["activity_id"],
                "household_id": item["household_id"],
                "activity_type": item["activity_type"],
                "summary": item["summary"],
                "details": json.loads(item.get("details_json", "{}")),
                "occurred_at": item["occurred_at"],
            }
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _corrupt("activity", item.get("activity_id")) from exc


def _plan_to_item(plan: Plan, household_id: str) -> dict[str, Any]:
    return {
        "plan_id": plan.id,
        "household_id": household_id,
        "created_at": _utc_iso(plan.created_at),
        "payload": plan.model_dump_json(),
    }


def _plan_from_item(item: dict[str, Any]) -> Plan:
    payload = item.get("payload")
    if not isinstance(payload, str):
        raise _corrupt("plan", item.get("plan_id"))
    try:
        return Plan.model_validate_json(payload)
    except ValidationError as exc:  # also covers invalid JSON
        raise _corrupt("plan", item.get("plan_id")) from exc


def _corrupt(kind: str, identifier: Any) -> PersistenceError:
    return PersistenceError(
        "corrupt", f"stored {kind} {identifier!r} could not be decoded", operation=f"read_{kind}"
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _query_all(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    exclusive_start_key: dict[str, Any] | None = None
    while True:
        page_kwargs = dict(kwargs)
        if exclusive_start_key:
            page_kwargs["ExclusiveStartKey"] = exclusive_start_key
        response = table.query(**page_kwargs)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            return items


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime values stored in DynamoDB must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()
