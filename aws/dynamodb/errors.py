"""Translate boto3/botocore DynamoDB failures into `PersistenceError`.

Classification is duck-typed on the exception class name and, for botocore
``ClientError``, on ``response["Error"]["Code"]`` so this module needs no
botocore import and the categories can be unit-tested with plain fakes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from haven.memory.errors import PersistenceError

_ERROR_CODE_CATEGORIES: dict[str, str] = {
    "AccessDeniedException": "access_denied",
    "UnrecognizedClientException": "credentials",
    "InvalidSignatureException": "credentials",
    "ExpiredTokenException": "credentials",
    "ExpiredToken": "credentials",
    "InvalidClientTokenId": "credentials",
    "ResourceNotFoundException": "table_unavailable",
    "ResourceInUseException": "table_unavailable",
    "ProvisionedThroughputExceededException": "throttled",
    "ThrottlingException": "throttled",
    "RequestLimitExceeded": "throttled",
    "LimitExceededException": "throttled",
    "InternalServerError": "unavailable",
    "ServiceUnavailable": "unavailable",
    "ServiceUnavailableException": "unavailable",
    "RequestTimeout": "timeout",
    "RequestTimeoutException": "timeout",
    "ConditionalCheckFailedException": "conflict",
    "TransactionConflictException": "conflict",
    "ValidationException": "validation",
    "ItemCollectionSizeLimitExceededException": "validation",
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
    "NoRegionError": "validation",
    "EndpointConnectionError": "network",
    "ConnectionClosedError": "network",
    "ConnectionError": "network",
    "ConnectTimeoutError": "timeout",
    "ReadTimeoutError": "timeout",
    "TimeoutError": "timeout",
}


def error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error") or {}
        if isinstance(error, Mapping):
            return str(error.get("Code") or "")
    return ""


def wrap_dynamodb_error(exc: Exception, *, operation: str) -> PersistenceError:
    """Classify a DynamoDB/botocore failure. Never includes item payloads."""

    if isinstance(exc, PersistenceError):
        return exc
    name = type(exc).__name__
    code = error_code(exc)
    category = _ERROR_CODE_CATEGORIES.get(code) or _EXCEPTION_NAME_CATEGORIES.get(name, "provider")
    return PersistenceError(category, f"{code or name}", operation=operation)


@contextmanager
def dynamodb_errors(operation: str) -> Iterator[None]:
    """Convert any provider exception raised inside into `PersistenceError`."""

    try:
        yield
    except PersistenceError:
        raise
    except Exception as exc:
        raise wrap_dynamodb_error(exc, operation=operation) from exc


__all__ = ["dynamodb_errors", "error_code", "wrap_dynamodb_error"]
