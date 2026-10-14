"""A small in-memory imitation of a boto3 DynamoDB ``Table`` resource.

Supports exactly what Haven's repositories use: ``put_item`` (with the
``attribute_not_exists`` conditional form), ``get_item``, ``delete_item`` and
``query`` with ``Key(...).eq`` / ``begins_with`` conditions, ``ScanIndexForward``
and ``Limit``. Failures can be injected per operation so repository error
handling is testable without AWS.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from boto3.dynamodb.conditions import And, BeginsWith, ConditionBase, Equals
from botocore.exceptions import ClientError


def client_error(code: str, operation: str = "PutItem", message: str | None = None) -> ClientError:
    """Build a botocore ``ClientError`` the way boto3 would raise it."""

    return ClientError(
        {"Error": {"Code": code, "Message": message or f"{code} (simulated)"}},
        operation,
    )


class FakeTable:
    """In-memory table keyed by a hash key and an optional range key."""

    def __init__(self, name: str, hash_key: str, range_key: str | None = None) -> None:
        self.name = name
        self.hash_key = hash_key
        self.range_key = range_key
        self.items: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # operation name -> exception factory (called once per invocation)
        self.failures: dict[str, Callable[[], BaseException]] = {}

    # -- failure injection ------------------------------------------------- #

    def fail(self, operation: str, exc: BaseException | Callable[[], BaseException]) -> None:
        self.failures[operation] = exc if callable(exc) else (lambda e=exc: e)

    def clear_failures(self) -> None:
        self.failures.clear()

    def _maybe_fail(self, operation: str) -> None:
        factory = self.failures.get(operation)
        if factory is not None:
            raise factory()

    # -- table API ------------------------------------------------------------ #

    def put_item(self, *, Item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_item", {"Item": Item, **kwargs}))
        self._maybe_fail("put_item")
        key = self._key_of(Item)
        condition = kwargs.get("ConditionExpression")
        if condition is not None and key in self.items:
            match = re.fullmatch(r"\s*attribute_not_exists\((\w+)\)\s*", condition)
            if match and match.group(1) in self.items[key]:
                raise client_error("ConditionalCheckFailedException", "PutItem")
        self.items[key] = dict(Item)
        return {}

    def get_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_item", {"Key": Key, **kwargs}))
        self._maybe_fail("get_item")
        item = self.items.get(self._key_of(Key))
        return {"Item": dict(item)} if item is not None else {}

    def delete_item(self, *, Key: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_item", {"Key": Key, **kwargs}))
        self._maybe_fail("delete_item")
        item = self.items.pop(self._key_of(Key), None)
        if kwargs.get("ReturnValues") == "ALL_OLD" and item is not None:
            return {"Attributes": dict(item)}
        return {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("query", kwargs))
        self._maybe_fail("query")
        condition = kwargs["KeyConditionExpression"]
        matched = [item for item in self.items.values() if _matches(condition, item)]
        if self.range_key:
            matched.sort(key=lambda i: i[self.range_key])
        if kwargs.get("ScanIndexForward") is False:
            matched.reverse()
        start = kwargs.get("ExclusiveStartKey")
        if start is not None:
            keys = [self._key_of(i) for i in matched]
            matched = matched[keys.index(self._key_of(start)) + 1 :]
        limit = kwargs.get("Limit")
        response: dict[str, Any] = {}
        if limit is not None and len(matched) > limit:
            page = matched[:limit]
            response["LastEvaluatedKey"] = {
                k: page[-1][k] for k in (self.hash_key, self.range_key) if k
            }
            matched = page
        response["Items"] = [dict(i) for i in matched]
        response["Count"] = len(matched)
        return response

    # -- helpers --------------------------------------------------------------- #

    def _key_of(self, item: dict[str, Any]) -> tuple[Any, ...]:
        if self.range_key:
            return (item[self.hash_key], item[self.range_key])
        return (item[self.hash_key],)

    def ops(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeDynamoDBResource:
    """Mimics ``boto3.resource("dynamodb")`` for `create_dynamodb_repositories`."""

    def __init__(self, tables: Iterable[FakeTable]) -> None:
        self._tables = {t.name: t for t in tables}

    def Table(self, name: str) -> FakeTable:  # noqa: N802 (boto3 naming)
        return self._tables[name]


def haven_tables(prefix: str = "Haven") -> tuple[FakeTable, FakeTable, FakeTable]:
    """Tables with the same keys as `aws.dynamodb.tables.ensure_tables` creates."""

    return (
        FakeTable(f"{prefix}Preferences", "household_id", "preference_id"),
        FakeTable(f"{prefix}Activities", "household_id", "activity_key"),
        FakeTable(f"{prefix}Plans", "plan_id"),
    )


def _matches(condition: ConditionBase, item: dict[str, Any]) -> bool:
    if isinstance(condition, And):
        return all(_matches(part, item) for part in condition._values)
    if isinstance(condition, Equals):
        attr, expected = condition._values
        return item.get(attr.name) == expected
    if isinstance(condition, BeginsWith):
        attr, prefix = condition._values
        value = item.get(attr.name)
        return isinstance(value, str) and value.startswith(prefix)
    raise NotImplementedError(f"FakeTable does not support {type(condition).__name__}")
