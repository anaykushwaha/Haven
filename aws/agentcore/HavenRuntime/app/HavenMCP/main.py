"""Not the production MCP server.

`agentcore create` generated this stub. The deployed runtime does **not**
load it. `aws/agentcore/HavenRuntime/agentcore/agentcore.json` points at:

    entrypoint: aws/agentcore/runtime.py
    codeLocation: ../../..   (the repository root)

That entrypoint wraps `haven.mcp.server.create_server` so AgentCore hosts
Haven's existing tools rather than a second implementation. Local and
deployed runtimes both use that path.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "Haven's MCP server is aws/agentcore/runtime.py "
        "(haven.mcp.server.create_server). This stub is not the entrypoint."
    )
