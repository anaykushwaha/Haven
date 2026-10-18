"""Run Haven's MCP server.

Usage:
    python scripts/run_server.py
    python scripts/run_server.py --transport stdio
    python scripts/run_server.py --host 0.0.0.0 --port 8787
    python scripts/run_server.py --dashboard-api

Host/port default to HAVEN_MCP_HOST / HAVEN_MCP_PORT when set, falling
back to 127.0.0.1:8787; CLI flags always win over both. The env vars
exist so a container (AgentCore, Docker) can configure binding without
needing to override the command — see apps/alexa/README.md for the
production values Person A's deployment sets.

Alexa+ requires Streamable HTTP (the default here); `--transport stdio`
is for local tools that speak MCP over stdio, such as the MCP Inspector,
which is worth running against this server before pointing the Alexa+
web simulator at it (see apps/alexa/README.md).

`--dashboard-api` (or HAVEN_DASHBOARD_API=1) additionally serves the
read-only JSON the Haven dashboard (apps/dashboard) polls, at
GET /api/dashboard, on the same port. It is OFF by default on purpose:
that endpoint exposes household state, and the MCP port is often
tunnelled to the public internet for Alexa+. Turn it on for a local demo,
and point the tunnel at a server started without it.

Requires the package to be installed (`pip install -e .`).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from haven.mcp.server import create_server

DEFAULT_HOST = os.environ.get("HAVEN_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("HAVEN_MCP_PORT", "8787"))
DEFAULT_DASHBOARD_API = os.environ.get("HAVEN_DASHBOARD_API", "").lower() in ("1", "true", "yes")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Haven's MCP server.")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio"],
        default="streamable-http",
        help="MCP transport (default: streamable-http, required by Alexa+).",
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Host to bind for streamable-http (default: {DEFAULT_HOST})."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind for streamable-http (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--dashboard-api",
        action="store_true",
        default=DEFAULT_DASHBOARD_API,
        help="Also serve the read-only dashboard JSON at /api/dashboard (streamable-http only; off by default).",
    )
    args = parser.parse_args(argv)

    if args.dashboard_api and args.transport != "streamable-http":
        parser.error("--dashboard-api needs --transport streamable-http")

    if args.transport == "stdio":
        server = create_server()
        print("Haven MCP server: stdio")
        server.run(transport="stdio")
        return 0

    server = create_server(host=args.host, port=args.port)

    if not args.dashboard_api:
        print(f"Haven MCP server: http://{args.host}:{args.port} (streamable-http)")
        server.run(transport="streamable-http")
        return 0

    # Serve the MCP app and the dashboard API from one process, so the
    # dashboard reads the same household the MCP tools are changing.
    import uvicorn

    from haven.mcp.dashboard import DashboardApiMiddleware

    app = DashboardApiMiddleware(server.streamable_http_app())
    print(f"Haven MCP server: http://{args.host}:{args.port} (streamable-http)")
    print(f"Dashboard API:    http://{args.host}:{args.port}/api/dashboard (read-only)")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
