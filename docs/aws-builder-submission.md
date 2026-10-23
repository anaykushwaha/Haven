# Haven — AWS Builder Submission Material

Paste-ready AWS-specific copy. Shorten later if a form imposes a
limit; this repository does not define hackathon character counts.

## One-Sentence AWS Summary

Haven is an Alexa+ household agent whose MCP server runs on Amazon
Bedrock AgentCore, plans with Strands on Amazon Bedrock, and remembers
only explicit preferences and activity through Amazon DynamoDB.

## How Haven Uses AWS

Haven is not `Alexa → LLM`. The implemented path is:

```text
Alexa+
   ↓
MCP
   ↓
AgentCore
   ↓
Haven orchestration
   ├── context
   ├── deterministic constraints
   ├── Bedrock reasoning
   ├── policy enforcement
   ├── deterministic execution
   ├── verification
   └── privacy-aware persistence
```

AI decides what makes sense. Deterministic code decides what is
allowed. Execution code performs only validated actions against a
simulated household runtime. Verification checks the result.

## AWS Architecture

- **AgentCore Runtime (`HavenMCP`)** hosts the existing Haven MCP
  server as stateless Streamable HTTP (`0.0.0.0:8000/mcp`) in a
  Container built from the repository-root `Dockerfile`.
- **Amazon Cognito + CUSTOM_JWT** authenticates every public call
  before Haven runs.
- **Amazon Bedrock** (via Strands structured output) selects among
  accepted planning candidates when
  `HAVEN_PLANNER_BACKEND=bedrock`.
- **Amazon DynamoDB** stores preferences, activity history, and plan
  documents behind repository adapters.
- **IAM** splits runtime item/model/log access from table
  provisioning.
- **Amazon CloudWatch** receives AgentCore runtime logs.

Local defaults are `mock` + in-memory stores so developers and CI
never need AWS. Production sets `bedrock` + `dynamodb` explicitly.
There is no silent fallback.

## Why AgentCore

Alexa+ is an MCP client. AgentCore is the managed MCP runtime: public
HTTPS, CUSTOM_JWT, a Container execution role, and CloudWatch logs.
Haven did not reimplement the product for AWS. `aws/agentcore/runtime.py`
wraps `haven.mcp.server.create_server`.

AgentCore Memory is unused. Haven's memory is application-owned
DynamoDB.

## Why Bedrock

Planning needs a model that can choose and order among a small set of
already-accepted actions and write a short summary. Strands invokes
Bedrock with `structured_output_model=ModelPlanDecision`. The model
returns candidate IDs. Application code rebuilds `PlanStep`s, so
Bedrock cannot invent devices, rooms, or risk classes, and cannot
execute the house.

## Why DynamoDB

AgentCore workers are stateless. Preferences and activity history must
outlive a single request. Three on-demand tables
(`HavenPreferences`, `HavenActivities`, `HavenPlans`) sit behind
repositories so `MemoryService` stays backend-agnostic. Transient
household state is rejected before it can become durable memory.

## Security and Authentication

```text
client → Cognito access token → Authorization: Bearer
      → AgentCore CUSTOM_JWT → Haven
```

The runtime uses its execution role for Bedrock and DynamoDB. No AWS
keys, Cognito passwords, or JWTs are stored in the repository or the
container image. Runtime IAM cannot create or delete tables.
High-impact household actions require an explicit confirmation flag
even after a plan exists.

## Privacy Architecture

Context is gathered for the current goal, not as a standing
surveillance feed. Only explicit preferences and small activity
metadata persist. Presence, occupancy, lock state, and similar
transient keys are blocked from durable storage. The optional
dashboard API is local-only and disabled by default.

## Technical Differentiation

| Common shortcut | Haven |
| --- | --- |
| Alexa → LLM → tools | Alexa+ → MCP → AgentCore → Haven layers |
| Model emits executable actions | Model selects among application-built candidates |
| Planning is execution | `create_plan` returns a `Plan`; execution is a later tool |
| Cloud flips on when credentials exist | Explicit `HAVEN_PLANNER_BACKEND` / `HAVEN_PERSISTENCE_BACKEND` |
| Provider failure becomes a mock success | Bedrock and DynamoDB errors surface with categories |
| Agent memory product | Application MemoryService + DynamoDB |

The demo household is a **simulated household runtime**, not physical
smart-home hardware.

## Reliability / Failure Handling

- Invalid config refuses to start (`ValueError` on unknown backends).
- Bedrock failures raise `ModelInvocationError`; no mock fallback.
- Invalid model selections raise `ModelDecisionError`.
- DynamoDB failures raise `PersistenceError`; no in-memory fallback.
- Duplicate plan writes are idempotent or raise `PlanConflictError`.
- Missing/invalid JWTs die at AgentCore (401/403).
- Partial execution is a recorded result, then verified.

## Demo Proof Points

- Offline: `pytest` (default excludes `aws_live`) and in-process MCP
  through the real AgentCore entrypoint.
- Deploy contract: Container + CUSTOM_JWT + runtime env checked by
  `tests/integration/test_aws_flow.py`.
- Live (when the AWS session and JWT are available):
  `pytest -m aws_live` for authenticated MCP, Bedrock planning, and
  DynamoDB activity persistence.
- Dated evidence: `docs/aws-production-verification.md`.

Do not claim a live box that file marks BLOCKED or NOT RUN.

## Open-Source / Reproducibility

- Apache 2.0 (`LICENSE`)
- `.env.example` names every non-secret variable
- `docs/architecture.md`, `docs/aws.md`, `docs/deployment.md`
- Operator DynamoDB setup: `python scripts/setup_dynamodb.py`
- AgentCore project: `aws/agentcore/HavenRuntime/`
- CI: `.github/workflows/test.yml` runs offline pytest on Python 3.11
  and 3.13

## AWS Services Used

- Amazon Bedrock AgentCore Runtime
- Amazon Bedrock
- Strands Agents (Bedrock model integration)
- Amazon DynamoDB
- Amazon Cognito (CUSTOM_JWT)
- AWS IAM
- Amazon CloudWatch Logs
- Amazon ECR / CodeBuild (AgentCore Container image pipeline)
