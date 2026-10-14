"""Shared helpers for AgentCore/AWS integration tests.

Everything here is offline. The in-process MCP client drives Haven's real
AgentCore entrypoint (``aws.agentcore.runtime``) through an ASGI transport,
so the deployment contract (stateless Streamable HTTP at ``/mcp``) is
exercised without a network, Docker, or AWS.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTCORE_DIR = REPO_ROOT / "aws" / "agentcore"
PROJECT_CONFIG = AGENTCORE_DIR / "HavenRuntime" / "agentcore" / "agentcore.json"
TARGETS_CONFIG = AGENTCORE_DIR / "HavenRuntime" / "agentcore" / "aws-targets.json"
RUNTIME_ENV_FILE = AGENTCORE_DIR / "config" / "haven-runtime.env"
POLICY_DIR = REPO_ROOT / "aws" / "iam" / "policies"

LIVE_FLAG = "HAVEN_AWS_LIVE"
LIVE_URL = "HAVEN_AGENTCORE_RUNTIME_URL"
LIVE_TOKEN = "HAVEN_AGENTCORE_BEARER_TOKEN"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines, ignoring comments and blanks."""

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@pytest.fixture(scope="session")
def project_spec() -> dict[str, Any]:
    return load_json(PROJECT_CONFIG)


@pytest.fixture(scope="session")
def haven_runtime_spec(project_spec: dict[str, Any]) -> dict[str, Any]:
    runtimes = [r for r in project_spec["runtimes"] if r["name"] == "HavenMCP"]
    assert len(runtimes) == 1, "exactly one HavenMCP runtime must be configured"
    return runtimes[0]


@pytest.fixture
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the local/offline backends regardless of the developer's shell."""

    monkeypatch.setenv("HAVEN_PLANNER_BACKEND", "mock")
    monkeypatch.setenv("HAVEN_PERSISTENCE_BACKEND", "memory")
    monkeypatch.delenv("HAVEN_DYNAMODB_ENDPOINT_URL", raising=False)


def _text_content(result: Any) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


class McpProbe:
    """Thin wrapper over a connected ``ClientSession`` for readable tests."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def tool_names(self) -> list[str]:
        tools = await self.session.list_tools()
        return sorted(tool.name for tool in tools.tools)

    async def call(self, name: str, **arguments: Any) -> tuple[bool, str]:
        result = await self.session.call_tool(name, arguments)
        return bool(result.isError), _text_content(result)

    async def call_json(self, name: str, **arguments: Any) -> dict[str, Any]:
        is_error, text = await self.call(name, **arguments)
        assert not is_error, f"{name} failed: {text}"
        return json.loads(text)


@asynccontextmanager
async def _connect(
    url: str,
    http_client: httpx.AsyncClient,
) -> AsyncIterator[McpProbe]:
    async with streamable_http_client(url, http_client=http_client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield McpProbe(session)


ProbeFactory = Callable[..., Any]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def inprocess_mcp(offline_env: None) -> ProbeFactory:
    """Serve Haven's AgentCore entrypoint in-process.

    ``async with inprocess_mcp() as connect`` starts one server process
    (one Starlette lifespan, exactly like one AgentCore runtime instance);
    ``async with connect() as probe`` opens one stateless MCP connection,
    exactly like one AgentCore request. Opening several connections inside
    one ``serve`` block therefore mimics several requests hitting the same
    deployed runtime.
    """

    import importlib

    from haven.mcp.server import reset_app_context

    reset_app_context()  # a fresh "process" for every test
    runtime = importlib.import_module("aws.agentcore.runtime")
    server = runtime.build_server()
    app = server.streamable_http_app()

    @asynccontextmanager
    async def serve() -> AsyncIterator[ProbeFactory]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://haven.test", timeout=30
            ) as client:

                @asynccontextmanager
                async def connect() -> AsyncIterator[McpProbe]:
                    async with _connect("http://haven.test/mcp", client) as probe:
                        yield probe

                yield connect

    return serve


def live_settings() -> tuple[str, str] | None:
    """Return (url, token) when live tests are explicitly enabled and configured."""

    if os.environ.get(LIVE_FLAG, "").strip() != "1":
        return None
    url = os.environ.get(LIVE_URL, "").strip()
    token = os.environ.get(LIVE_TOKEN, "").strip()
    if not url or not token:
        return None
    return url, token


@pytest.fixture
def live_mcp() -> ProbeFactory:
    """Connect to the deployed AgentCore runtime with a caller-supplied JWT.

    Skips (never fails) unless ``HAVEN_AWS_LIVE=1``,
    ``HAVEN_AGENTCORE_RUNTIME_URL`` and ``HAVEN_AGENTCORE_BEARER_TOKEN`` are
    all set in the environment. The token is read from the process
    environment only and is never written to disk or logs.
    """

    settings = live_settings()
    if settings is None:
        pytest.skip(
            "live AgentCore test skipped: set HAVEN_AWS_LIVE=1, "
            f"{LIVE_URL} and {LIVE_TOKEN} to run it"
        )
    url, token = settings

    @asynccontextmanager
    async def connect(*, bearer: str | None = token) -> AsyncIterator[McpProbe]:
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        async with httpx.AsyncClient(headers=headers, timeout=60) as client:
            async with _connect(url, client) as probe:
                yield probe

    return connect
