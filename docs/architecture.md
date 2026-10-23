# Haven Architecture

This document describes the architecture that is implemented in this
repository. It does not describe planned or speculative components.

## Overview

Haven is a household agent for Alexa+. A spoken or typed goal arrives
over MCP. Haven resolves only the context that goal needs, proposes a
structured `Plan`, optionally executes that plan against a simulated
household runtime, and then verifies the result.

Planning is not execution. The planner returns a `Plan`. It does not
manipulate household devices. Amazon Bedrock, when selected, only
chooses among candidates that application code already accepted.
Deterministic code then builds the executable steps, enforces policy,
performs actions, and checks outcomes.

The primary product path is Alexa+ → MCP → AgentCore → Haven. The
React dashboard and household/media/security simulators are supporting
development and demo layers, not the production interface.

## End-to-End Request Flow

```text
Alexa+
   ↓
MCP (Streamable HTTP)
   ↓
Amazon Bedrock AgentCore Runtime
   ↓
Haven MCP Server
   ↓
Context Resolver
   ↓
Planner
   ↓
Executor
   ↓
Verifier
```

Supporting services used during planning and memory:

```text
Planner
   ↓
Strands Agents
   ↓
Amazon Bedrock
```

```text
Haven Memory
   ↓
Amazon DynamoDB
```

A typical MCP request such as `propose_household_plan_tool` does this:

1. Load the current simulated household snapshot and media catalog.
2. Resolve a `HouseholdContext` from approved sources only.
3. Classify the goal (`movie_night`, `leaving_home`, `tonight_planning`,
   or `unknown`) with deterministic keyword rules.
4. Generate action candidates from that context.
5. Filter candidates with deterministic constraints.
6. Ask the selected reasoner (mock or Bedrock) to choose among accepted
   candidates.
7. Rebuild trusted `PlanStep` objects from those candidates and apply
   policy.
8. Store the plan in the process-local `PlanStore`.
9. Record a `PLAN_PROPOSED` activity through `MemoryService`.

Execution is a later, explicit tool call. Verification always runs as
part of execution; it is not a separate MCP tool.

## Core Components

### Alexa+ Interface

Person B owns the Alexa+ add-on materials in `apps/alexa/`. Alexa+ is
an MCP client. It does not embed Haven's planner or talk to Bedrock
directly. Production Alexa+ is expected to call the AgentCore MCP
endpoint with a Cognito JWT. Local development can target
`scripts/run_server.py` through a tunnel.

### MCP Server

`src/haven/mcp/server.py` builds a FastMCP v1 server named `haven`.
The AgentCore wrapper in `aws/agentcore/runtime.py` hosts that same
server; it is not a second implementation.

Registered tools (MCP names):

| Tool | Role |
| --- | --- |
| `get_household_context_tool` | Resolve household context for a goal |
| `propose_household_plan_tool` | Context → planner → structured plan |
| `get_plan_status_tool` | Look up a previously proposed plan |
| `execute_household_plan_tool` | Execute + verify a stored plan |
| `save_household_preference_tool` | Persist an explicit preference |
| `get_activity_recommendations_tool` | Deterministic media suggestions |

Tool logic lives in `src/haven/mcp/tools/` as plain functions.
`register()` is the only place those functions are wrapped as MCP tools.

The dashboard JSON API (`GET /api/dashboard`, `GET /api/health`) is an
optional local wrapper in `src/haven/mcp/dashboard.py`. It is off by
default and is not included in the AgentCore container.

### Context Layer

`src/haven/context/resolver.py` gathers only the facts a goal needs
from approved sources:

- user statements
- current device/sensor state from the household simulator
- saved preferences and activity history via `MemoryService.lookup_fact`
- calendar availability via `CalendarService`

The resolver does not invent a complete home model. Most of
`HouseholdContext` is mapped from the current household snapshot.
Scalar facts such as `available_minutes`, `preferred_room_id`, and
`preferred_genres` go through the source-priority engine.

### Planning Layer

Planning is split across:

- `src/haven/planning/workflows.py` — deterministic workflow classification
- `src/haven/agents/planner.py` — candidate generation, backend selection,
  plan assembly
- `src/haven/planning/constraints.py` — deterministic filtering
- `src/haven/agents/policies.py` — confirmation and risk policy
- `src/haven/agents/bedrock_reasoner.py` — Strands + Bedrock structured
  selection
- `src/haven/planning/plan_service.py` — public orchestration + policy
  validation

`planning ≠ execution`. `create_plan(goal, context) -> Plan` returns a
structured plan. It does not call the executor, the verifier, Alexa, or
any household device.

### Execution Layer

`src/haven/execution/executor.py` dispatches each `PlanStep` to a
deterministic handler in `src/haven/execution/actions.py`. No LLM is
involved. High-impact steps require an explicit `confirmed=True` from
the caller. Temporary device failures are retried once; unsupported
actions fail immediately.

The current household runtime is simulated
(`simulator/household`, `simulator/media`, `simulator/security`). Haven
does not currently drive physical smart-home hardware.

### Verification Layer

`src/haven/verification/verifier.py` is read-only. After execution it
checks whether the simulated household actually reached the expected
state. `src/haven/verification/recovery.py` then classifies what to do
about mismatches. Verification never re-executes an action.

### Memory Layer

Callers depend on `src/haven/memory/service.py`. The facade talks to a
`PreferenceStore` and an `ActivityStore`. Backend selection is explicit:

```text
local:
MemoryService
   ↓
in-memory stores
```

```text
production:
MemoryService
   ↓
DynamoDB repositories
```

`HAVEN_PERSISTENCE_BACKEND=memory` (default) never imports AWS.
`HAVEN_PERSISTENCE_BACKEND=dynamodb` lazily constructs DynamoDB
adapters from `aws/dynamodb/repositories.py`.

Durable memory is privacy-scoped. Transient household state (presence,
occupancy, lock state, and similar keys in
`haven.memory.preferences.TRANSIENT_KEYS`) cannot be saved as a
preference or written into activity details.

Plan *objects* used by MCP live in the process-local `PlanStore` in
`src/haven/mcp/server.py`. Proposed-plan *history* is written through
`MemoryService.record_plan_proposed` into the activity store
(`HavenActivities` in production). A DynamoDB plan repository for the
`HavenPlans` table exists and is idempotent, but the MCP path does not
currently persist full `Plan` payloads through `MemoryService`.

## AWS Architecture

### AgentCore Runtime

Project: `aws/agentcore/HavenRuntime/`
Runtime name: `HavenMCP`
Protocol: MCP
Build: Container (`Dockerfile` at the repository root)
Entrypoint: `aws/agentcore/runtime.py`
Network: PUBLIC
Auth: CUSTOM_JWT
Region: `us-west-2`

The wrapper serves the existing Haven MCP server as stateless
Streamable HTTP on `0.0.0.0:8000` at `/mcp`. AgentCore Memory is not
part of Haven's architecture; durable memory is `MemoryService` →
DynamoDB.

### Amazon Bedrock

Used only for planner reasoning when
`HAVEN_PLANNER_BACKEND=bedrock`. Configuration is environment-driven
(`aws/bedrock/config.py`). The Strands `BedrockModel` is constructed in
`aws/bedrock/client.py` and invoked from
`src/haven/agents/bedrock_reasoner.py`. Bedrock does not execute
household actions.

### DynamoDB

Three tables: `HavenPreferences`, `HavenActivities`, `HavenPlans`.
Provisioning lives in `aws/dynamodb/tables.py`. Application code talks
to repositories, not to boto3 table APIs directly.

### Cognito / CUSTOM_JWT

Clients present a Cognito access token as `Authorization: Bearer ...`.
AgentCore validates the JWT before the request reaches Haven. Haven
never stores the token.

### IAM

Runtime policies (`aws/iam/policies/*-runtime.json`) are attached to
the AgentCore execution role. Table creation is a separate provisioning
policy used by `scripts/setup_dynamodb.py`. The runtime cannot create
or delete tables.

### CloudWatch

The runtime execution role can write AgentCore runtime logs under
`/aws/bedrock-agentcore/runtimes/*`. Haven does not ship a custom
observability product.

## Local Development Architecture

```text
scripts/run_server.py
   ↓
haven.mcp.server.create_server
   ↓
simulators + MemoryService(in-memory) + mock planner
```

Defaults:

- `HAVEN_PLANNER_BACKEND=mock`
- `HAVEN_PERSISTENCE_BACKEND=memory`
- MCP on `127.0.0.1:8787`
- dashboard API off unless `--dashboard-api`

The AgentCore entrypoint can also be run locally (same server, port
8000) for production-shaped wiring checks. Offline pytest drives that
entrypoint in-process through `tests/integration/test_aws_flow.py`.

## Production Architecture

```text
Alexa+ or MCP client
   ↓  Authorization: Bearer <Cognito access token>
AgentCore HavenMCP (CUSTOM_JWT)
   ↓
aws/agentcore/runtime.py
   ↓
Haven MCP (stateless Streamable HTTP)
   ├── HAVEN_PLANNER_BACKEND=bedrock
   └── HAVEN_PERSISTENCE_BACKEND=dynamodb
```

Production values are declared in
`aws/agentcore/config/haven-runtime.env` and mirrored into
`aws/agentcore/HavenRuntime/agentcore/agentcore.json`. There is no
silent fallback from Bedrock to mock, and no silent fallback from
DynamoDB to in-memory stores.

## Safety Boundaries

Planning and execution are separate. The LLM safety boundary is:

```text
context
   ↓
candidate generation
   ↓
deterministic constraints
   ↓
accepted candidates
   ↓
Bedrock / Strands reasoning
   ↓
validated selection
   ↓
application-created PlanSteps
```

The model receives temporary candidate IDs (`candidate-1`, …) and
returns a `ModelPlanDecision`. Application code then:

- rejects unknown or duplicate IDs
- rebuilds steps from the original `ActionCandidate` objects
- reapplies confirmation policy from `ActionRisk`

The model cannot invent devices, rooms, actions, or risk classes. A
high-impact step (`HIGH_IMPACT_ACTION`) always requires confirmation
before execution. Bedrock failures raise
`ModelInvocationError`; Haven does not hide them behind the mock
planner.

## Privacy Boundaries

- Context is goal-scoped, not a continuous household transcript.
- Only explicit preferences and small activity metadata are durable.
- Transient state cannot become memory.
- Activity details reject surveillance-like keys.
- The optional dashboard API is local-only and disabled by default
  because it exposes household state.
- JWTs, passwords, and AWS credentials are not stored in Haven
  application state or committed files.

## Failure Boundaries

| Layer | Failure behavior |
| --- | --- |
| Planner backend unknown | `ValueError`; process does not start with a bad config |
| Bedrock invocation | `ModelInvocationError` with a category; no mock fallback |
| Invalid model selection | `ModelDecisionError`; no trusted plan is assembled |
| DynamoDB write/read | `PersistenceError` with a category; no in-memory fallback |
| Duplicate plan id | `PlanConflictError` unless the payload is identical |
| Missing / invalid JWT | AgentCore returns 401/403; Haven is not reached |
| High-impact execution without confirmation | plan remains `AWAITING_CONFIRMATION` |
| Partial execution | recorded as a normal `ExecutionResult`, then verified |

## Repository Mapping

| Concept | Implementation |
| --- | --- |
| MCP server | `src/haven/mcp/server.py` |
| MCP tools | `src/haven/mcp/tools/` |
| MCP schemas | `src/haven/mcp/schemas.py` |
| Context resolver | `src/haven/context/resolver.py` |
| Planner | `src/haven/agents/planner.py` |
| Bedrock reasoner | `src/haven/agents/bedrock_reasoner.py` |
| Policies | `src/haven/agents/policies.py` |
| Prompts | `src/haven/agents/prompts.py` |
| Workflows / constraints | `src/haven/planning/` |
| Plan service | `src/haven/planning/plan_service.py` |
| Runtime config | `src/haven/config.py` |
| Executor | `src/haven/execution/executor.py` |
| Verifier | `src/haven/verification/verifier.py` |
| Memory facade | `src/haven/memory/` |
| AgentCore entrypoint | `aws/agentcore/runtime.py` |
| AgentCore project | `aws/agentcore/HavenRuntime/` |
| Bedrock connection | `aws/bedrock/` |
| DynamoDB | `aws/dynamodb/` |
| IAM policies | `aws/iam/policies/` |
| Household / media simulators | `simulator/` |
| Alexa+ materials | `apps/alexa/` |
| Dashboard | `apps/dashboard/` |
| Offline AWS contract tests | `tests/integration/test_aws_flow.py` |
| Offline MCP flow tests | `tests/integration/test_mcp_flow.py` |
| Workflow tests | `tests/workflows/` |

## Design Decisions

**Host the existing MCP server in AgentCore.** The runtime wrapper only
adapts host, port, path, and stateless HTTP. Haven is not reimplemented
for AWS.

**Container build, not CodeZip.** `pip install .` inside the image
makes `haven`, `aws`, and `simulator` importable the same way a local
editable install does.

**Explicit backend switches.** Presence of AWS credentials never
selects Bedrock or DynamoDB. Defaults stay offline (`mock` / `memory`).

**Deterministic constraints before the model.** Time, availability,
capability, and presence checks are ordinary code. The model only
orders accepted candidates.

**Policy is applied twice.** Confirmation flags are set when the plan
is assembled and enforced again by the executor.

**Simulated household runtime.** Demo and tests use local simulators so
the agent can be developed without physical devices.

**Least-privilege IAM, not perfect isolation.** Runtime policies are
scoped to Haven's tables, Bedrock invoke, and AgentCore log groups.
They still use region-wide account wildcards in ARNs and
foundation-model wildcards required by inference profiles.
