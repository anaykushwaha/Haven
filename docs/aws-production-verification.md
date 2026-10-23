# Haven AWS Production Verification

Date: 2026-10-23

This log records what was actually run. It does not treat
`agentcore.json`, the Dockerfile, or offline tests as a live deploy.

| Distinction | This run |
| --- | --- |
| Implemented | Yes — AgentCore project, runtime wrapper, IAM, DynamoDB, Bedrock |
| Locally tested | Yes — offline pytest, in-process MCP through `aws.agentcore.runtime` |
| Deployed live | No evidence — `deployed-state.json` is `{"targets": {}}` |
| Live-verified | BLOCKED — AWS login session expired |

## AgentCore

Status: **BLOCKED**
Runtime: `HavenMCP` (configured, not shown deployed)
Region: `us-west-2`
Protocol: MCP (configured)
Build: Container (configured)
Result: `aws sts get-caller-identity` with `AWS_PROFILE=haven-dev`
returned `Your session has expired. Please reauthenticate using
'aws login'.` Exit code 255.

`agentcore validate` from `aws/agentcore/HavenRuntime`: **Valid**
(exit 0). `agentcore status --` reported `target: dev, us-west-2`
and `HavenMCP: Local only`.

`agentcore deploy --dry-run` without a target failed:
`Target "default" not found in aws-targets.json`.
`agentcore deploy --dry-run --target dev` loaded the `dev` target,
then failed at "Validate project" with
`No AWS credentials configured` (expired `aws login` session).
`agentcore deploy` (live) was not executed.
`aws/agentcore/HavenRuntime/agentcore/.cli/deployed-state.json`
remains:

```json
{"targets": {}}
```

Local configuration checks: `HavenMCP`, protocol MCP, Container,
entrypoint `aws/agentcore/runtime.py`, CUSTOM_JWT, production env
`HAVEN_PLANNER_BACKEND=bedrock` /
`HAVEN_PERSISTENCE_BACKEND=dynamodb`. Unused AgentCore Memory
resource `HavenMCPMemory` was removed from `agentcore.json`
(`memories: []`). Haven memory remains `MemoryService` → DynamoDB.

## Authentication

CUSTOM_JWT: **implemented** in `agentcore.json` (discovery URL +
allowed client). Not live-verified.
Remote authenticated request: **NOT RUN**
Result: no fresh Cognito access token was requested because AWS login
itself is expired. Offline tests simulate 401/403 for missing /
invalid / expired bearer tokens
(`tests/integration/test_aws_flow.py`).

## MCP

initialize: **PASS** (local process `python aws/agentcore/runtime.py`
on `0.0.0.0:8000`, plus in-process AgentCore entrypoint tests)
tools/list: **PASS** (six tools: context, propose, status, execute,
preference, recommendations)
safe tools/call (`get_household_context_tool`): **PASS**
(`household-001`)
planning (`propose_household_plan_tool`, mock backend): **PASS**
(structured plan, 3 steps)
Result: local production-shaped wiring works with
`HAVEN_PLANNER_BACKEND=mock` /
`HAVEN_PERSISTENCE_BACKEND=memory`.
Remote MCP: **NOT RUN**

## Bedrock

Planning invocation: **NOT RUN** live
Model: `global.anthropic.claude-sonnet-4-6` (configured)
Result: production planning was not invoked. Offline planner /
reasoner tests pass, including "no silent mock fallback" contracts.
`python scripts/test_bedrock_connections.py` was not run against AWS
because the session is expired.

## DynamoDB

Preference persistence: **NOT RUN** live
Activity persistence: **NOT RUN** live
Plan persistence: **NOT RUN** live
Result: tables were not listed. Offline repository tests (including
idempotent `save_plan`) pass. Application-level live verification
requires `pytest -m aws_live`, which needs AWS + JWT.

## CloudWatch

Runtime logs visible: **NOT RUN**
Result: no deployed runtime to query. The runtime IAM policy for
`/aws/bedrock-agentcore/runtimes/*` is present and checked offline.

## Live Integration Tests

Command: `pytest -m aws_live`
Result: **NOT RUN** — `HAVEN_AWS_LIVE` was not set, AWS session
expired, no runtime URL, no bearer token. Default `pytest` correctly
deselected the four `aws_live` tests.

## Remaining Blockers

1. Interactive `aws login --profile haven-dev` must succeed before
   any live deploy or verification.
2. After login: `agentcore deploy --dry-run` then `agentcore deploy`
   from `aws/agentcore/HavenRuntime`.
3. Confirm tables with `python scripts/setup_dynamodb.py` and
   `aws dynamodb list-tables --region us-west-2`.
4. Obtain a fresh Cognito access token in the shell only.
5. Run `pytest -m aws_live` and a small Bedrock planning sample
   (maximum three safe goals).
6. Confirm CloudWatch runtime logs without capturing JWTs or
   household payloads into the repo.

Until those complete, AWS production claims must stay
**implemented + locally tested**, not **deployed / live-verified**.
