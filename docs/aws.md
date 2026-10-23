# Haven AWS Usage

This is the authoritative description of the AWS services Haven actually
uses. Every service below appears in code or deployment configuration.
Services that are not used — including AgentCore Memory — are not part
of the product architecture.

For each service this document answers: what it does, why Haven uses
it, where it lives in the repository, how it is secured, and how local
mode differs from production.

## Amazon Bedrock

**Role.** Planner reasoning only. Bedrock never executes household
actions and never writes memory.

**Why.** When a goal has accepted action candidates, Haven needs a
model to choose and order among those candidates and to write a short
summary. Bedrock supplies that model through Strands.

**Model configuration.** Environment-driven via `aws/bedrock/config.py`:

| Variable | Default |
| --- | --- |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | `us-west-2` |
| `HAVEN_BEDROCK_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` |
| `HAVEN_BEDROCK_TEMPERATURE` | `0.2` |
| `HAVEN_BEDROCK_MAX_TOKENS` | `2048` |

Credentials are not read here. boto3 uses the normal AWS credential
chain (profile, SSO, or the AgentCore execution role).

**Integration.**

```text
HAVEN_PLANNER_BACKEND=bedrock
   ↓
haven.agents.planner.select_reasoner
   ↓
haven.agents.bedrock_reasoner.BedrockReasoner
   ↓
aws.bedrock.client.create_bedrock_model
   ↓
strands.Agent(..., structured_output_model=ModelPlanDecision)
```

Files:

- `aws/bedrock/config.py`
- `aws/bedrock/client.py`
- `src/haven/agents/bedrock_reasoner.py`
- `src/haven/agents/planner.py`

The model must return a `ModelPlanDecision`: a summary plus
`selected_candidate_ids` drawn from the IDs Haven offered. Unknown IDs,
duplicates, empty selections without clarification, and missing
clarification prompts are rejected. Trusted `PlanStep` objects are then
rebuilt from the original candidates.

**Local vs production.**

```text
local:
HAVEN_PLANNER_BACKEND=mock
```

```text
production:
HAVEN_PLANNER_BACKEND=bedrock
```

The switch is explicit. Haven never selects Bedrock just because
credentials exist, and never falls back to mock when Bedrock fails.
Provider failures become `ModelInvocationError` with a category such as
`access_denied`, `throttled`, `timeout`, or `structured_output`.

**Security.** The runtime role may call `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on foundation models and
inference profiles (`aws/iam/policies/haven-bedrock-runtime.json`).
No access keys are stored in the application.

## Strands Agents

**Role.** Structured-output client in front of Bedrock.

**Why.** Haven needs a typed `ModelPlanDecision`, not free-form JSON
parsing. Strands `Agent(..., structured_output_model=ModelPlanDecision)`
is that boundary.

**Where.** `src/haven/agents/bedrock_reasoner.py` (`invoke_strands_structured`)
and `aws/bedrock/client.py` (`BedrockModel`). Strands is imported lazily
so mock mode stays offline.

**Local vs production.** Unused when `HAVEN_PLANNER_BACKEND=mock`.
Used only on the Bedrock path.

**Security.** Strands receives no AWS keys from Haven source. It uses
the Bedrock model object, which authenticates through boto3.

## Amazon Bedrock AgentCore Runtime

**Role.** Hosted MCP runtime for the existing Haven server.

**Why.** Alexa+ speaks MCP over the public internet. AgentCore provides
the managed MCP endpoint, CUSTOM_JWT authentication, container hosting,
and the execution role used to call Bedrock and DynamoDB.

**Relationship.**

```text
AgentCore
   ↓
aws/agentcore/runtime.py
   ↓
haven.mcp.server.create_server
```

The wrapper does not duplicate Haven. It only satisfies the AgentCore
MCP contract:

- protocol: MCP
- transport: Streamable HTTP
- bind: `0.0.0.0:8000`
- path: `/mcp`
- `stateless_http=True`

**Deployment project.** `aws/agentcore/HavenRuntime/`

Current runtime configuration
(`aws/agentcore/HavenRuntime/agentcore/agentcore.json`):

| Field | Value |
| --- | --- |
| Runtime | `HavenMCP` |
| Protocol | MCP |
| Build | Container |
| Dockerfile | repository-root `Dockerfile` |
| Entrypoint | `aws/agentcore/runtime.py` |
| `codeLocation` | repository root (`../../..` from the project) |
| Network | PUBLIC |
| Auth | CUSTOM_JWT |
| Region | `us-west-2` |
| Planner | `HAVEN_PLANNER_BACKEND=bedrock` |
| Persistence | `HAVEN_PERSISTENCE_BACKEND=dynamodb` |
| Forwarded headers | `Authorization` only |

This is a Container deployment, not CodeZip. The image runs
`pip install .` so `haven`, `aws`, and `simulator` are real installed
packages.

**Local vs production.** Locally, `scripts/run_server.py` or
`agentcore dev --logs` from `aws/agentcore/HavenRuntime` can serve the
same server. Deploy previews must pass `--target dev`. Production is the deployed `HavenMCP` runtime. Offline
tests exercise the entrypoint in-process
(`tests/integration/test_aws_flow.py`) without AWS.

**Security.** AgentCore validates the Cognito JWT before Haven runs.
The process uses its execution role. No bearer token is written to
disk by Haven.

**Unused generated resource.** Haven does not use AgentCore Memory.
Durable memory is `MemoryService` → DynamoDB. The deployment
configuration keeps `memories: []` so an unused Memory resource is not
provisioned.

## Amazon DynamoDB

**Role.** Durable persistence for explicit preferences, activity
history, and (via a dedicated repository) plan documents.

**Why.** Production runtimes are stateless HTTP workers. Preferences
and activity history must survive process restarts and be readable by
the context layer.

**Three-table design.**

| Table | Key | Used for |
| --- | --- | --- |
| `HavenPreferences` | `household_id` + `preference_id` | Explicit preferences |
| `HavenActivities` | `household_id` + `activity_key` | Activity / audit history |
| `HavenPlans` | `plan_id` | Full `Plan` documents |

Files:

- `aws/dynamodb/config.py`
- `aws/dynamodb/client.py`
- `aws/dynamodb/tables.py`
- `aws/dynamodb/repositories.py`
- `src/haven/memory/factory.py`

Repositories isolate application code from DynamoDB. `MemoryService`
depends on `PreferenceStore` and `ActivityStore` protocols. The
DynamoDB classes implement those protocols; local in-memory classes
implement the same ones.

`DynamoDBPlanRepository.save_plan` is idempotent: it writes with
`attribute_not_exists(plan_id)`. Re-saving the identical payload is a
no-op. A different payload under the same id raises `PlanConflictError`
unless `overwrite=True`.

The live MCP path persists preferences and activity records through
`MemoryService`. Proposed plans are recorded as `PLAN_PROPOSED`
activities (including `plan_id`, workflow, step count). The in-process
`PlanStore` still holds the executable `Plan` for
`get_plan_status_tool` / `execute_household_plan_tool` on that runtime
instance. The `HavenPlans` repository is implemented and tested, but
`MemoryService` does not currently call `save_plan`.

**Local vs production.**

```text
local:
HAVEN_PERSISTENCE_BACKEND=memory
```

```text
production:
HAVEN_PERSISTENCE_BACKEND=dynamodb
```

Tables are created by the operator (`python scripts/setup_dynamodb.py`),
not by the runtime. Failures become `PersistenceError` with a category;
Haven does not fake a successful write in memory.

**Security.** Runtime IAM is item-level on the three Haven tables only.
Provisioning IAM is a separate operator policy.

## Amazon Cognito

**Role.** Issues the access tokens that AgentCore's CUSTOM_JWT
authorizer accepts.

**Why.** The MCP endpoint is public. Haven must not be callable without
a token from the configured user pool and app client.

**Architecture.**

```text
client
   ↓
Cognito access token
   ↓
Authorization: Bearer ...
   ↓
AgentCore CUSTOM_JWT validation
   ↓
Haven runtime
```

`agentcore.json` stores a discovery URL and allowed client id. Those
are identifiers, not credentials. Public docs should treat them as
`<COGNITO_USER_POOL_ID>` and `<COGNITO_CLIENT_ID>`. The live values
live in the AgentCore project for deployment, not in this narrative.

Haven never handles user passwords, refresh tokens, or client secrets.
Live tests read a temporary access token from
`HAVEN_AGENTCORE_BEARER_TOKEN` in the calling shell only.

**Local vs production.** Local `scripts/run_server.py` has no JWT
gateway. AgentCore local/dev and the deployed runtime do.

## IAM

Haven separates **runtime permissions** from **provisioning
permissions**.

| Policy | Attached to | Purpose |
| --- | --- | --- |
| `aws/iam/policies/haven-bedrock-runtime.json` | AgentCore execution role | `InvokeModel` / `InvokeModelWithResponseStream` |
| `aws/iam/policies/haven-dynamodb-runtime.json` | AgentCore execution role | Get/Put/Update/Delete/Query/Describe on the three Haven tables |
| `aws/iam/policies/haven-cloudwatch-logs-runtime.json` | AgentCore execution role | Create/describe/put on AgentCore runtime log groups |
| `aws/iam/policies/haven-dynamodb-provisioning.json` | Operator identity | `CreateTable` / `DescribeTable` / `ListTables` |

`tests/integration/test_aws_flow.py` asserts:

- no `"Action": "*"` or service-wide `dynamodb:*`
- runtime policies cannot `CreateTable` / `DeleteTable` / `UpdateTable`
- no embedded access keys or tokens

This is scoped, reviewable IAM. It is not perfect least privilege:
Bedrock resources are `foundation-model/*` and `inference-profile/*`,
DynamoDB ARNs use `us-west-2:*`, and CloudWatch can `DescribeLogGroups`
on `*`. Those shapes match the current prototype.

The CDK-managed AgentCore role may also grant equivalent Bedrock and
log permissions. Haven still attaches the JSON policies so the
application's intended access is explicit in the repository.

## Amazon CloudWatch

**Role.** Destination for AgentCore / runtime process logs.

**Why.** Diagnose runtime start, request failures, Bedrock errors, and
DynamoDB errors. Haven does not implement custom metrics, traces, or
dashboards.

**Where.** `haven-cloudwatch-logs-runtime.json` allows writes to
`arn:aws:logs:us-west-2:*:log-group:/aws/bedrock-agentcore/runtimes/*`.
Application logs use the process `HAVEN_LOG_LEVEL`.

**What to expect.** Startup lines from `haven.mcp.server` (household
id, planner backend, persistence backend), planner backend selection,
and categorized provider/persistence errors. Logs must not contain
JWTs, passwords, or AWS keys. Payload echoing of household context is
avoided in persistence error paths.

OTEL is disabled in `agentcore.json` (`enableOtel: false`).

## Local vs production summary

| Concern | Local / offline | Production |
| --- | --- | --- |
| Planner | `mock` | `bedrock` (Strands → Bedrock) |
| Persistence | in-memory stores | DynamoDB repositories |
| MCP process | `scripts/run_server.py` or in-process tests | AgentCore `HavenMCP` container |
| Auth | none on the local script | CUSTOM_JWT (Cognito) |
| AWS credentials | not required | execution role + caller JWT |
| Household devices | simulators | the same simulators inside the container |
| Dashboard API | optional, local only | not in the container |

## Cost / hackathon scope

Haven uses small-scale hackathon and demo infrastructure: one AgentCore
runtime, three on-demand DynamoDB tables, a Cognito user pool, and
occasional Bedrock invocations for planning. Production cost planning,
reserved capacity, and multi-tenant isolation are outside the current
prototype scope. This document does not invent dollar amounts.
