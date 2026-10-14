"""Amazon Bedrock AgentCore Runtime entrypoint for Haven's MCP server.

    Alexa+ / MCP client
        --Streamable HTTP + Authorization: Bearer <Cognito JWT>-->
    AgentCore Runtime (CUSTOM_JWT authorizer, protocol=MCP)
        --> this process: Haven MCP server on 0.0.0.0:8000/mcp
            --> haven.* (planning via Bedrock, memory via DynamoDB)

This file only adapts Haven's existing server to the AgentCore MCP contract:
port 8000, path ``/mcp``, stateless Streamable HTTP. It is not a second Haven
implementation; every tool comes from ``haven.mcp.server.create_server``.

Configuration arrives through environment variables set on the runtime (see
``aws/agentcore/HavenRuntime/agentcore/agentcore.json`` and
``aws/agentcore/config/``). AWS credentials come from the runtime's execution
role; nothing here reads or stores credentials or bearer tokens.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from haven.mcp.server import create_server

# AgentCore Runtime MCP contract: listen on 0.0.0.0:8000 and serve stateless
# Streamable HTTP at /mcp (FastMCP's default path).
AGENTCORE_HOST = "0.0.0.0"
AGENTCORE_PORT = 8000


def build_server() -> FastMCP:
    """Build Haven's MCP server configured for the AgentCore Runtime contract.

    Separate from the module-level ``mcp`` so tests can serve a fresh
    instance in-process through the exact same code path.
    """

    return create_server(host=AGENTCORE_HOST, port=AGENTCORE_PORT, stateless_http=True)


mcp = build_server()


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
