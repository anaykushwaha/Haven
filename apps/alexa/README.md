# Haven + Alexa+

How to point Alexa+ at Haven's MCP server, locally and in production.

> **A note on certainty.** Alexa+'s add-on/MCP connection tooling is a
> very new part of Amazon's developer platform, and this document was
> written without access to a live Alexa+ developer account to confirm
> the exact console/CLI workflow. `apps/alexa/config/addon.json` is
> modeled on Amazon's existing Alexa Skills Kit manifest format
> (`manifestVersion`, `storeListing`, locales, `privacyAndCompliance`)
> with an `integrations` block added for the MCP endpoint — a reasonable
> best guess, not a confirmed Alexa+ add-on schema. The pieces fully
> within Haven's control — the server, its tools, and their schemas —
> are built and tested (`tests/integration/test_mcp_flow.py`). Anything
> below that depends on Amazon's own tooling is marked "confirm against
> the real console" and logged in `docs/friction-log.md`. Start with
> Amazon's own Alexa+ MCP quickstart documentation for anything that
> looks stale here.

## What this integration looks like

```
Alexa+  --MCP (Streamable HTTP)-->  Haven MCP Server  -->  Haven
                                                             (context / planning / execution / verification / memory)
```

Haven exposes 6 tools over MCP (`haven/mcp/`): `get_household_context`,
`propose_household_plan`, `get_plan_status`, `execute_household_plan`,
`save_household_preference`, and `get_activity_recommendations`. See
`apps/alexa/config/tool_catalog.json` for each tool's parameters and
example phrasing (finer-grained than the handful of `examplePhrases` in
`addon.json`'s store listing), and `apps/alexa/schemas/*.schema.json` for
their full request/response JSON Schema — generated from
`haven/mcp/schemas.py` by `python scripts/export_mcp_schemas.py`;
regenerate it whenever a tool's shape changes (`--check` exits 1 if the
checked-in files are stale).

## Files in this directory

- `config/addon.json` — the Alexa+ add-on manifest: store listing
  (name, description, example phrases, icons, privacy links) and the
  `integrations` block pointing at Haven's MCP endpoint. This is what
  you'd actually submit/configure in the Alexa+ console — update its
  `integrations[0].config.endpoints.default.uri` to wherever your server
  currently runs (tunnel URL locally, AgentCore URL in production).
- `config/.env.example` — local-only values for the Alexa+ tooling
  itself (your tunnel URL, optional MCP Inspector port overrides). Not
  read by any Haven Python code — copy to `.env` and fill in.
- `config/tool_catalog.json` — a per-tool reference: parameters,
  example utterances, and which tool conditionally needs `confirmed`.
  Useful for development and for Amazon's own tooling if it accepts
  per-tool phrase hints separately from the store listing.
- `schemas/*.schema.json` — generated request/response JSON Schema per
  tool.

## Prerequisites

- Python 3.11+, with Haven installed: `pip install -e ".[dev]"`
- Access to Alexa+'s add-on/MCP developer tooling (beta access as of
  this writing — see Amazon's Alexa+ MCP quickstart for current
  requirements)
- Node.js, for the MCP Inspector (`npx @modelcontextprotocol/inspector`)
  — recommended before touching Alexa+ at all
- A tunnel tool (ngrok, Cloudflare Tunnel, or similar) for local
  development — **Alexa+ runs in Amazon's cloud and cannot reach
  `127.0.0.1` on your machine directly.** The Alexa+ web simulator may
  have its own tunneling support; confirm against current Amazon docs
  before assuming you need a separate tool.

## Local development setup

1. Start Haven's MCP server:

   ```bash
   python scripts/run_server.py
   # Haven MCP server: http://127.0.0.1:8787 (streamable-http)
   ```

2. Sanity-check it with the MCP Inspector before involving Alexa+ at
   all:

   ```bash
   npx @modelcontextprotocol/inspector
   ```

   The inspector's UI defaults to client port `6274` and proxy port
   `6277` (see `config/.env.example` if either conflicts with something
   already running). Point it at `http://127.0.0.1:8787/mcp` (the path
   FastMCP's Streamable HTTP transport serves on by default — confirm
   against your installed `mcp` version if this 404s; see
   `docs/friction-log.md`). You should see all 6 tools listed. Try
   `propose_household_plan` with `goal: "Get movie night ready"`, then
   `execute_household_plan` with the returned `plan_id`.

3. Expose the local server through a tunnel:

   ```bash
   ngrok http 8787
   ```

   Copy the resulting `https://...ngrok...` URL.

4. Copy `config/.env.example` to `config/.env` and set `MCP_SERVER_URL`
   to that tunnel URL plus `/mcp`. Also update
   `config/addon.json`'s `integrations[0].config.endpoints.default.uri`
   to the same value — the manifest is what you'll actually hand to the
   Alexa+ console.

5. In the Alexa+ developer console (or whatever Amazon's current tooling
   calls it), create/configure the add-on using `config/addon.json` if
   it supports importing a manifest directly, or copy its values in by
   hand otherwise:
   - Server URL: the `integrations[0].config.endpoints.default.uri` you
     just set
   - Transport: Streamable HTTP
   - Protocol version: `2025-11-25` or later, per Amazon's current
     requirement — Haven's server doesn't pin a specific protocol
     version itself; this is a constraint from Alexa+'s side

6. Use example phrases from `config/tool_catalog.json` (or the shorter
   list in `addon.json`'s `examplePhrases`) in the Alexa+ web simulator
   to test each tool.

## Switching to production

Once Person A's AgentCore deployment is live:

1. Update `config/addon.json`'s
   `integrations[0].config.endpoints.default.uri` to the AgentCore URL
   Person A provides.
2. Re-point the Alexa+ console's add-on configuration at that URL.
3. Re-run the example phrases from `tool_catalog.json` against
   production before considering this done — nothing about the local
   tunnel setup proves AgentCore's networking, IAM, or environment
   variables are correct.

## Testing procedure

In order of how early each catches a problem:

1. **Unit/offline tests** — don't need a running server at all:
   ```bash
   pytest
   ```
2. **MCP integration tests** — build the real server and its lifespan
   context in-process and drive the actual tool functions, without a
   network hop:
   ```bash
   pytest tests/integration/test_mcp_flow.py -v
   ```
3. **MCP Inspector** — a real client speaking the real protocol against
   a running server (step 2 in "Local development setup" above).
4. **Alexa+ web simulator** — the first point this exercises Amazon's
   own NLU/intent-to-tool mapping, which nothing earlier in this list
   touches.
5. **Manual phrase walkthrough** — for each workflow, in order:
   - "What should we do tonight?" → `get_activity_recommendations_tool`
   - "Get movie night ready." → `propose_household_plan_tool` → confirm
     the proposed plan makes sense → "Go ahead." →
     `execute_household_plan_tool`
   - "What's the status of that?" → `get_plan_status_tool`
   - "We like sci-fi and comedy." → `save_household_preference_tool`,
     then repeat the recommendation phrase and confirm the preference
     changed the results

## Required environment values

Set these wherever Haven's MCP server actually runs (locally in your
shell, or as container environment variables for Person A's AgentCore
deployment) — distinct from `apps/alexa/config/.env`, which configures
the Alexa+ tooling side, not Haven itself:

| Variable | Default | Purpose |
|---|---|---|
| `HAVEN_PLANNER_BACKEND` | `mock` | `mock` (offline, deterministic) or `bedrock`. Keep `mock` until Bedrock access is confirmed working (see `docs/friction-log.md` entries from Phase 2/3). |
| `HAVEN_PERSISTENCE_BACKEND` | `memory` | `memory` (in-process, resets on restart) or `dynamodb`. Production should use `dynamodb` once Person A's tables exist — otherwise saved preferences and activity history vanish every time the container restarts. |
| `HAVEN_MCP_HOST` | `127.0.0.1` | Bind address for `scripts/run_server.py`. Containers should set this to `0.0.0.0`. |
| `HAVEN_MCP_PORT` | `8787` | Bind port for `scripts/run_server.py`. |
| `HAVEN_MAX_PLAN_STEPS` | `12` | Safety cap on plan size (see `haven.config`). |
| `HAVEN_LOG_LEVEL` | `INFO` | Server log verbosity. |

AWS credentials, Bedrock model settings, and DynamoDB table names are
configured separately (see `aws/bedrock/config.py`, `aws/dynamodb/config.py`,
and whatever `docs/aws.md` says once Person A writes it) — this server
never reads AWS credentials directly.

## Known gaps (see `docs/friction-log.md` for full detail)

- `addon.json`'s exact shape is modeled on the classic Alexa Skills Kit
  manifest, not confirmed as what Alexa+'s add-on tooling actually
  expects.
- The Streamable HTTP path (`/mcp`) and the exact `mcp.server.fastmcp`
  constructor/`call_tool` surface used in `haven/mcp/server.py` and
  `tests/integration/test_mcp_flow.py` were built against the documented
  pattern but not run against a real, network-installed `mcp` package in
  this environment.
- No authentication is configured on the MCP server yet (see
  `config/.env.example`'s `MCP_AUTH_TOKEN` placeholder). Fine behind a
  private tunnel for development; needs revisiting before anything
  resembling a real deployment.
