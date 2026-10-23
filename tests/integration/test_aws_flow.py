"""AgentCore deployment contract: AgentCore -> Haven MCP -> Bedrock / DynamoDB.

Two layers:

* ``integration`` (default, offline): the checked-in AgentCore project,
  IAM policies, environment and entrypoint agree with each other and with
  Haven's own configuration loaders; the real entrypoint serves MCP
  in-process (initialize, tools/list, a READ tool).
* ``aws_live`` (opt-in): the deployed runtime answers over HTTPS with a
  Cognito JWT, planning goes through Bedrock, and the resulting activity is
  visible through Haven's DynamoDB repositories. Enabled only with
  ``HAVEN_AWS_LIVE=1`` plus the runtime URL and a bearer token in the
  environment; otherwise these tests skip with an explicit reason.

Nothing here bypasses JWT authentication or embeds credentials.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from aws.bedrock.config import load_bedrock_config
from aws.dynamodb.config import load_dynamodb_config
from haven.config import HavenConfig

from .conftest import (
    LIVE_FLAG,
    LIVE_TOKEN,
    LIVE_URL,
    POLICY_DIR,
    REPO_ROOT,
    RUNTIME_ENV_FILE,
    TARGETS_CONFIG,
    load_json,
    parse_env_file,
)

pytestmark = pytest.mark.integration

READ_TOOL = "get_household_context_tool"
EXPECTED_TOOLS = {
    "get_household_context_tool",
    "propose_household_plan_tool",
    "get_plan_status_tool",
    "execute_household_plan_tool",
    "save_household_preference_tool",
    "get_activity_recommendations_tool",
}
CREDENTIAL_ENV_NAMES = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
SECRET_PATTERN = re.compile(
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|aws_secret_access_key|eyJ[A-Za-z0-9_-]{20,}\.",
    re.IGNORECASE,
)
TABLE_LIFECYCLE_ACTIONS = {
    "dynamodb:CreateTable",
    "dynamodb:DeleteTable",
    "dynamodb:UpdateTable",
    "dynamodb:*",
}


# --------------------------------------------------------------------------- #
# agentcore.json contract
# --------------------------------------------------------------------------- #


def test_runtime_identity_and_protocol(haven_runtime_spec: dict[str, Any]) -> None:
    assert haven_runtime_spec["name"] == "HavenMCP"
    assert haven_runtime_spec["protocol"] == "MCP"
    assert haven_runtime_spec["networkMode"] == "PUBLIC"
    assert haven_runtime_spec["entrypoint"] == "aws/agentcore/runtime.py"
    assert haven_runtime_spec["requestHeaderAllowlist"] == ["Authorization"]


def test_runtime_uses_cognito_jwt_authorizer(haven_runtime_spec: dict[str, Any]) -> None:
    assert haven_runtime_spec["authorizerType"] == "CUSTOM_JWT"
    jwt = haven_runtime_spec["authorizerConfiguration"]["customJwtAuthorizer"]
    assert jwt["discoveryUrl"].startswith("https://cognito-idp.us-west-2.amazonaws.com/")
    assert jwt["discoveryUrl"].endswith("/.well-known/openid-configuration")
    assert jwt["allowedClients"], "at least one allowed Cognito app client is required"
    # A client *secret* must never appear next to the client id.
    assert "clientSecret" not in json.dumps(jwt)


def test_entrypoint_and_build_inputs_exist(haven_runtime_spec: dict[str, Any]) -> None:
    # The CLI resolves codeLocation against the AgentCore project root
    # (the parent of the agentcore/ config directory).
    agentcore_project_root = REPO_ROOT / "aws" / "agentcore" / "HavenRuntime"
    code_location = (agentcore_project_root / haven_runtime_spec["codeLocation"]).resolve()
    assert code_location == REPO_ROOT, "codeLocation must be the repository root"
    assert (code_location / haven_runtime_spec["entrypoint"]).is_file()
    assert haven_runtime_spec["build"] == "Container"
    assert (code_location / haven_runtime_spec.get("dockerfile", "Dockerfile")).is_file()
    assert (code_location / ".dockerignore").is_file()
    dockerfile = (code_location / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install ." in dockerfile, "the image must install the project, not hack sys.path"
    assert 'CMD ["python", "aws/agentcore/runtime.py"]' in dockerfile


def test_runtime_env_matches_config_file_and_is_secret_free(
    haven_runtime_spec: dict[str, Any],
) -> None:
    env_vars = {item["name"]: item["value"] for item in haven_runtime_spec["envVars"]}
    assert env_vars == parse_env_file(RUNTIME_ENV_FILE)
    assert not CREDENTIAL_ENV_NAMES & env_vars.keys()
    assert env_vars["AWS_REGION"] == "us-west-2"
    assert env_vars["HAVEN_PLANNER_BACKEND"] == "bedrock"
    assert env_vars["HAVEN_PERSISTENCE_BACKEND"] == "dynamodb"
    assert env_vars["HAVEN_DYNAMODB_PREFERENCES_TABLE"] == "HavenPreferences"
    assert env_vars["HAVEN_DYNAMODB_ACTIVITIES_TABLE"] == "HavenActivities"
    assert env_vars["HAVEN_DYNAMODB_PLANS_TABLE"] == "HavenPlans"
    assert env_vars["HAVEN_BEDROCK_MODEL_ID"]
    for value in env_vars.values():
        assert not SECRET_PATTERN.search(value)


def test_runtime_env_is_accepted_by_haven_loaders(haven_runtime_spec: dict[str, Any]) -> None:
    env = {item["name"]: item["value"] for item in haven_runtime_spec["envVars"]}

    config = HavenConfig(
        planner_backend=env["HAVEN_PLANNER_BACKEND"],
        persistence_backend=env["HAVEN_PERSISTENCE_BACKEND"],
        max_plan_steps=int(env["HAVEN_MAX_PLAN_STEPS"]),
        log_level=env["HAVEN_LOG_LEVEL"],
    )
    assert (config.planner_backend, config.persistence_backend) == ("bedrock", "dynamodb")

    bedrock = load_bedrock_config(env)
    assert bedrock.region == "us-west-2"
    assert bedrock.model_id == env["HAVEN_BEDROCK_MODEL_ID"]

    dynamodb = load_dynamodb_config(env)
    assert dynamodb.region == "us-west-2"
    assert dynamodb.endpoint_url is None, "the deployed runtime must use the real DynamoDB endpoint"
    assert (dynamodb.preferences_table, dynamodb.activities_table, dynamodb.plans_table) == (
        "HavenPreferences",
        "HavenActivities",
        "HavenPlans",
    )


def test_deployment_target_is_us_west_2() -> None:
    targets = load_json(TARGETS_CONFIG)
    assert len(targets) == 1
    assert targets[0]["region"] == "us-west-2"
    assert re.fullmatch(r"[0-9]{12}", targets[0]["account"])


def test_no_credential_providers_or_secrets_in_project(project_spec: dict[str, Any]) -> None:
    assert project_spec["credentials"] == [], "Haven uses its execution role, not stored API keys"
    assert project_spec.get("memories", []) == [], (
        "Haven persists through MemoryService → DynamoDB, not AgentCore Memory"
    )
    assert not SECRET_PATTERN.search(json.dumps(project_spec))


# --------------------------------------------------------------------------- #
# IAM
# --------------------------------------------------------------------------- #


def _statements(path: Path) -> list[dict[str, Any]]:
    doc = load_json(path)
    assert doc["Version"] == "2012-10-17"
    return doc["Statement"]


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return action if isinstance(action, list) else [action]


@pytest.mark.parametrize("policy_file", sorted(POLICY_DIR.glob("*.json")), ids=lambda p: p.name)
def test_policies_have_no_wildcard_actions_or_secrets(policy_file: Path) -> None:
    text = policy_file.read_text(encoding="utf-8")
    assert not SECRET_PATTERN.search(text)
    for statement in _statements(policy_file):
        assert statement["Effect"] == "Allow"
        for action in _actions(statement):
            assert action != "*"
            assert not action.endswith(":*"), (
                f"service-wide wildcard {action!r} in {policy_file.name}"
            )


def test_runtime_policies_cannot_manage_tables() -> None:
    runtime_policies = sorted(POLICY_DIR.glob("*-runtime.json"))
    assert runtime_policies
    for policy_file in runtime_policies:
        for statement in _statements(policy_file):
            assert not TABLE_LIFECYCLE_ACTIONS & set(_actions(statement)), policy_file.name


def test_dynamodb_runtime_policy_is_scoped_to_haven_tables() -> None:
    statements = _statements(POLICY_DIR / "haven-dynamodb-runtime.json")
    resources = {res for st in statements for res in st["Resource"]}
    assert resources == {
        "arn:aws:dynamodb:us-west-2:*:table/HavenPreferences",
        "arn:aws:dynamodb:us-west-2:*:table/HavenActivities",
        "arn:aws:dynamodb:us-west-2:*:table/HavenPlans",
    }
    actions = {a for st in statements for a in _actions(st)}
    assert {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"} <= actions


def test_additional_policies_reference_existing_runtime_documents(
    haven_runtime_spec: dict[str, Any],
) -> None:
    attached = haven_runtime_spec["additionalPolicies"]
    required = {
        "aws/iam/policies/haven-bedrock-runtime.json",
        "aws/iam/policies/haven-cloudwatch-logs-runtime.json",
        "aws/iam/policies/haven-dynamodb-runtime.json",
    }
    assert required <= set(attached)
    for entry in attached:
        assert (REPO_ROOT / entry).is_file(), entry
        assert entry.endswith("-runtime.json"), "only runtime policies may be attached to the agent"


def test_ignore_files_keep_secrets_out() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore and ".env.*" in gitignore
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in dockerignore and ".env.*" in dockerignore
    assert "aws/agentcore/HavenRuntime" in dockerignore, (
        "CDK tooling and .env.local stay out of the image"
    )


# --------------------------------------------------------------------------- #
# Entrypoint serves MCP in-process (no AWS)
# --------------------------------------------------------------------------- #


def test_entrypoint_wraps_create_server(offline_env: None) -> None:
    import importlib

    runtime = importlib.import_module("aws.agentcore.runtime")
    settings = runtime.mcp.settings
    assert runtime.mcp.name == "haven"
    assert (settings.host, settings.port) == ("0.0.0.0", 8000)
    assert settings.stateless_http is True
    source = (REPO_ROOT / "aws" / "agentcore" / "runtime.py").read_text(encoding="utf-8")
    assert "sys.path" not in source
    assert "create_server(" in source


@pytest.mark.anyio
async def test_inprocess_initialize_tools_list_and_read_tool(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect, connect() as probe:
        names = await probe.tool_names()
        assert EXPECTED_TOOLS <= set(names)

        context = await probe.call_json(READ_TOOL, goal="Get movie night ready")
        assert context["household_id"]
        assert context["rooms"] and context["devices"]


@pytest.mark.anyio
async def test_inprocess_plan_proposal_uses_mock_backend_offline(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect, connect() as probe:
        plan = await probe.call_json("propose_household_plan_tool", goal="Get movie night ready")
        assert plan["plan_id"].startswith("plan-")
        assert plan["steps"], "the mock backend must still produce a structured plan"


@pytest.mark.anyio
async def test_inprocess_plan_survives_separate_stateless_requests(inprocess_mcp) -> None:
    """AgentCore sends every MCP call as its own stateless request. A plan
    proposed in one request must be visible to `get_plan_status` in the
    next one, within the same runtime process."""

    async with inprocess_mcp() as connect:
        async with connect() as probe:
            plan = await probe.call_json(
                "propose_household_plan_tool", goal="Get movie night ready"
            )
        async with connect() as probe:
            status = await probe.call_json("get_plan_status_tool", plan_id=plan["plan_id"])
    assert status["plan_id"] == plan["plan_id"]


# --------------------------------------------------------------------------- #
# AgentCore / JWT / downstream failure contract (offline, simulated)
# --------------------------------------------------------------------------- #


def test_live_mode_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from .conftest import live_settings

    monkeypatch.delenv(LIVE_FLAG, raising=False)
    monkeypatch.setenv(LIVE_URL, "https://example.invalid/mcp")
    monkeypatch.setenv(LIVE_TOKEN, "not-a-real-token")
    assert live_settings() is None

    monkeypatch.setenv(LIVE_FLAG, "1")
    monkeypatch.delenv(LIVE_TOKEN, raising=False)
    assert live_settings() is None

    monkeypatch.setenv(LIVE_FLAG, "1")
    monkeypatch.setenv(LIVE_TOKEN, "   ")
    assert live_settings() is None


def test_invalid_runtime_env_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported planner backend"):
        HavenConfig(planner_backend="quantum")
    with pytest.raises(ValueError, match="unsupported persistence backend"):
        HavenConfig(persistence_backend="s3")


@pytest.mark.anyio
async def test_simulated_missing_jwt_is_unauthorized() -> None:
    status = await _simulated_gateway_status(headers={})
    assert status in {401, 403}


@pytest.mark.anyio
async def test_simulated_invalid_and_expired_jwt_are_unauthorized() -> None:
    assert await _simulated_gateway_status(headers={"Authorization": "Bearer not-a-jwt"}) in {
        401,
        403,
    }
    assert await _simulated_gateway_status(headers={"Authorization": "Bearer expired"}) in {
        401,
        403,
    }


@pytest.mark.anyio
async def test_simulated_agentcore_unavailable() -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down"))
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://agentcore.test") as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})


@pytest.mark.anyio
async def test_simulated_runtime_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("AgentCore invocation timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://agentcore.test") as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})


async def _simulated_gateway_status(*, headers: dict[str, str]) -> int:
    """Stand in for AgentCore's CUSTOM_JWT authorizer.

    The authorizer sits in front of Haven; a missing/invalid/expired bearer
    never reaches ``create_server``. This mock encodes that contract so CI
    can assert the expected HTTP statuses without a live runtime.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] in {"", "not-a-jwt", "expired"}:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, headers=headers, base_url="https://agentcore.test"
    ) as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1}
        )
        return response.status_code


# --------------------------------------------------------------------------- #
# Live (opt-in): deployed runtime, JWT, Bedrock and DynamoDB
# --------------------------------------------------------------------------- #

aws_live = pytest.mark.aws_live


@aws_live
@pytest.mark.anyio
async def test_live_initialize_and_list_tools_with_jwt(live_mcp) -> None:
    async with live_mcp() as probe:
        names = await probe.tool_names()
        assert EXPECTED_TOOLS <= set(names)


@aws_live
@pytest.mark.anyio
async def test_live_read_tool_with_jwt(live_mcp) -> None:
    async with live_mcp() as probe:
        context = await probe.call_json(READ_TOOL, goal="Get movie night ready")
        assert context["household_id"]


@aws_live
@pytest.mark.anyio
async def test_live_plan_goes_through_bedrock_and_lands_in_dynamodb(live_mcp) -> None:
    """propose_household_plan on the deployed runtime must use Bedrock
    (HAVEN_PLANNER_BACKEND=bedrock, no fallback) and record the proposal in
    HavenActivities through Haven's own repository path."""

    from aws.dynamodb.repositories import create_dynamodb_repositories
    from haven.memory.activity import ActivityType

    async with live_mcp() as probe:
        context = await probe.call_json(READ_TOOL, goal="Get movie night ready")
        plan = await probe.call_json("propose_household_plan_tool", goal="Get movie night ready")

    repositories = create_dynamodb_repositories()
    records = repositories.activity.list(
        context["household_id"], activity_type=ActivityType.PLAN_PROPOSED, limit=25
    )
    assert any(r.details.get("plan_id") == plan["plan_id"] for r in records)


@aws_live
@pytest.mark.anyio
async def test_live_rejects_missing_bearer_token(live_mcp) -> None:
    import httpx

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        async with live_mcp(bearer=None) as probe:
            await probe.tool_names()
    assert excinfo.value.response.status_code in {401, 403}
