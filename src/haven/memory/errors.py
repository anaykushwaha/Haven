"""Backend-neutral persistence failures.

Storage adapters (in-memory, DynamoDB) translate their native errors into
these so callers never depend on boto3/botocore exception types. The original
exception is preserved as ``__cause__``; messages never contain credentials or
stored payloads.
"""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """A durable store could not complete an operation.

    ``category`` is a short machine-readable tag:

    - ``access_denied``     the execution role lacks permission
    - ``credentials``       no/expired AWS credentials
    - ``table_unavailable`` table missing or not yet active
    - ``throttled``         capacity/request limits hit; safe to retry later
    - ``timeout``           the request timed out
    - ``network``           endpoint unreachable
    - ``unavailable``       service-side internal error
    - ``conflict``          a conditional write found different existing data
    - ``corrupt``           a stored payload could not be decoded
    - ``validation``        the request was rejected as malformed
    - ``provider``          anything else from the provider
    """

    def __init__(self, category: str, message: str, *, operation: str | None = None) -> None:
        prefix = f"[{category}]"
        if operation:
            prefix += f" {operation}:"
        super().__init__(f"{prefix} {message}")
        self.category = category
        self.operation = operation


class PlanConflictError(PersistenceError):
    """A plan with this id already exists with different content."""

    def __init__(self, plan_id: str) -> None:
        super().__init__(
            "conflict",
            f"plan {plan_id!r} already exists with different content; pass overwrite=True "
            "to replace it intentionally",
            operation="save_plan",
        )
        self.plan_id = plan_id


__all__ = ["PersistenceError", "PlanConflictError"]
