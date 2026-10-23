# Deploying Haven

This guide is for a developer who already has permission to use the
hackathon AWS account. It reproduces Person A's cloud deployment: three
DynamoDB tables, Cognito CUSTOM_JWT in front of AgentCore, and the
`HavenMCP` container runtime.

Do not put long-lived access keys in `.env`. Use the AWS credential
chain.

Commands below are PowerShell because this repository is developed on
Windows. The Python invocations are the same on other shells.

## Prerequisites

- Python 3.11+ (the package requires `datetime.UTC` and `enum.StrEnum`)
- Docker, if you will run `agentcore dev` with the Container build
- AWS CLI v2 with a profile that can reach `us-west-2`
- AgentCore CLI (`agentcore`)
- Access to the hackathon account's Cognito user pool / app client
- Permission to create the three Haven DynamoDB tables and to deploy
  the existing AgentCore project

Haven currently pins `mcp>=1.28,<2`. The server uses FastMCP v1 APIs
(`FastMCP(...)`, `@mcp.tool()`, `run(transport="streamable-http")`).
Do not upgrade to MCP 2 as part of a routine deploy.

## 1. Create a Python Environment

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

On Unix-like shells the activation line is `source .venv/bin/activate`.

## 2. Install Haven

```powershell
python -m pip install -e ".[dev]"
```

This installs `haven`, `aws`, and `simulator`, plus pytest/ruff.

Confirm:

```powershell
python -c "from haven.mcp.server import create_server; from aws.agentcore.runtime import build_server"
pytest
ruff check src aws tests scripts
```

`pytest` stays offline (`-m 'not aws_live'` is the default in
`pyproject.toml`).

## 3. Authenticate to AWS

```powershell
aws login --profile haven-dev
$env:AWS_PROFILE="haven-dev"
$env:AWS_REGION="us-west-2"
aws sts get-caller-identity
```

If `get-caller-identity` reports that the session has expired, run
`aws login --profile haven-dev` again. Do not export
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` into `.env`.

## 4. Configure Environment

```powershell
Copy-Item .env.example .env
```

`.env` is gitignored. For a **local** process keep the defaults:

```text
HAVEN_PLANNER_BACKEND=mock
HAVEN_PERSISTENCE_BACKEND=memory
```

The **deployed** runtime does not read your `.env`. Its environment is
declared in `aws/agentcore/config/haven-runtime.env` and mirrored into
`aws/agentcore/HavenRuntime/agentcore/agentcore.json`:

```text
HAVEN_PLANNER_BACKEND=bedrock
HAVEN_PERSISTENCE_BACKEND=dynamodb
```

## 5. Provision DynamoDB

From the repository root, with the AWS profile exported:

```powershell
python scripts/setup_dynamodb.py
```

(`scripts/` is not a Python package, so use the file path rather than
`-m scripts.setup_dynamodb`.)

The script is idempotent: existing tables are left alone. Then:

```powershell
aws dynamodb list-tables --region us-west-2
```

Expected tables:

```text
HavenPreferences
HavenActivities
HavenPlans
```

Table names can be overridden with `HAVEN_DYNAMODB_*_TABLE`, but the
checked-in runtime IAM and AgentCore env expect the names above.

## 6. Configure Cognito / JWT Authentication

AgentCore CUSTOM_JWT is already declared on the `HavenMCP` runtime:

```text
authorizerType = CUSTOM_JWT
discoveryUrl   = https://cognito-idp.us-west-2.amazonaws.com/<COGNITO_USER_POOL_ID>/.well-known/openid-configuration
allowedClients = [<COGNITO_CLIENT_ID>]
```

To use a different pool or client, edit
`aws/agentcore/HavenRuntime/agentcore/agentcore.json` and redeploy.
Do not put a user password, client secret, refresh token, or access
token in that file.

Callers must send:

```text
Authorization: Bearer <Cognito access token>
```

## 7. Verify AgentCore Configuration

The AgentCore project lives at `aws/agentcore/HavenRuntime/`.
`agentcore.json` is the source of truth. Do not hand-edit generated
CDK/CloudFormation as the normal deploy path.

```powershell
cd aws\agentcore\HavenRuntime
agentcore validate
```

Confirm the runtime still says:

```text
name        = HavenMCP
protocol    = MCP
build       = Container
entrypoint  = aws/agentcore/runtime.py
networkMode = PUBLIC
authorizer  = CUSTOM_JWT
```

The deploy target is `dev` in `us-west-2`
(`aws/agentcore/HavenRuntime/agentcore/aws-targets.json`).

## 8. Test AgentCore Locally

Still from `aws/agentcore/HavenRuntime`:

```powershell
agentcore dev --logs
```

That boots the Container entrypoint locally. For a thinner check
without the CLI, from the repository root with mock/memory backends:

```powershell
$env:HAVEN_PLANNER_BACKEND="mock"
$env:HAVEN_PERSISTENCE_BACKEND="memory"
python aws\agentcore\runtime.py
```

The process listens on `0.0.0.0:8000` at `/mcp`. Offline integration
tests already cover initialize, `tools/list`, a READ tool, and a mock
plan:

```powershell
pytest tests/integration/test_aws_flow.py
```

Do not aim this local process at real home hardware. The household
runtime is simulated.

## 9. Preview Deployment

From `aws/agentcore/HavenRuntime`:

```powershell
agentcore deploy --dry-run --target dev
```

`--target dev` is required. The CLI defaults to a target named
`default`, which this project does not define
(`aws-targets.json` names the hackathon account `dev`).

Review the synthesized resources. Expected: one MCP runtime
(`HavenMCP`), Container build from the root `Dockerfile`, CUSTOM_JWT,
and the three runtime IAM policies. There should be no AgentCore
Memory resource.

## 10. Deploy AgentCore

From the same directory, after a correct dry-run:

```powershell
agentcore deploy --target dev
```

This uses the configured `dev` / `us-west-2` target. The AgentCore CLI
updates generated state, including
`aws/agentcore/HavenRuntime/agentcore/.cli/deployed-state.json`.

Do not invent a second AgentCore project. Do not hand-write
`deployed-state.json` to fake a deployment.

An empty `"targets": {}` in that file means the current configuration
has **not** been shown to be deployed.

## 11. Check Deployment Status

```powershell
agentcore status
```

Record only non-secret facts: runtime name, ARN, region, state,
protocol. Do not copy invocation tokens into notes that will be
committed.

`agentcore logs` can stream runtime logs once the session is valid.

## 12. Obtain a Test JWT

Use the normal Cognito authentication flow for the configured app
client. Export the **access token** in your shell only:

```powershell
$env:HAVEN_AGENTCORE_BEARER_TOKEN="<temporary access token>"
```

Never commit the token. Never paste it into docs, screenshots,
`agentcore.json`, `.env.example`, or GitHub Actions files.

## 13. Verify Remote MCP

Set the deployed invocation URL (not a secret, but environment-specific):

```powershell
$env:HAVEN_AWS_LIVE="1"
$env:HAVEN_AGENTCORE_RUNTIME_URL="<runtime invocation URL>"
$env:HAVEN_AGENTCORE_BEARER_TOKEN="<temporary access token>"
```

The live tests perform MCP `initialize`, `tools/list`,
`get_household_context_tool`, and a planning call. Do not use
`execute_household_plan_tool` against high-impact steps (unlock door,
disable security) as a production smoke test.

## 14. Run Live AWS Integration Tests

```powershell
pytest -m aws_live
```

This is the opt-in marker defined in `pyproject.toml`. It requires all
three live environment variables. Default `pytest` remains offline and
will deselect these tests.

The live suite is intended to prove:

```text
AgentCore → authenticated MCP → Haven → Bedrock planning
Haven → DynamoDB activity persistence
```

Skipped tests are not passes. If AWS login, JWT issuance, Bedrock
model access, or table permissions fail, record the failure category
instead of changing architecture to hide it.

## 15. Troubleshooting

These are failures actually seen or encoded in this repository
(`docs/friction-log.md` and the offline AWS tests).

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `Your session has expired. Please reauthenticate using 'aws login'.` | SSO / `aws login` session ended | `aws login --profile haven-dev`, then `aws sts get-caller-identity` |
| `agentcore deploy` reports `No AWS credentials configured` | Same expired/missing login; CLI also suggests long-lived keys | Re-run `aws login --profile haven-dev`. Do not put access keys in `.env`. |
| `ModuleNotFoundError: boto3` / botocore | editable install missing | `python -m pip install -e ".[dev]"` |
| FastMCP constructor / `stateless_http` errors after a pip upgrade | MCP 2.x installed | Stay on `mcp>=1.28,<2`. Haven uses FastMCP v1 APIs. |
| `import haven` fails inside a packaged zip | CodeZip layout | This project deploys as Container so `pip install .` owns imports |
| CUSTOM_JWT 401/403 | missing, invalid, or expired bearer | Obtain a fresh Cognito access token; send `Authorization: Bearer ...` |
| `ModelInvocationError` `access_denied` / account verification | Bedrock model not enabled for the account | Enable the configured model in `us-west-2`; do not silently switch to mock in production |
| DynamoDB `ResourceNotFoundException` | tables not created | `python scripts/setup_dynamodb.py` |
| DynamoDB `AccessDeniedException` | wrong identity or missing policy | Runtime role needs `haven-dynamodb-runtime.json`; setup needs the provisioning policy |
| `Target "default" not found in aws-targets.json` | CLI default target name is `default` | Pass `--target dev` (the only target in this project) |
| `deployed-state.json` is `"targets": {}` | CLI has not recorded a deploy | Run `agentcore deploy --target dev` from `aws/agentcore/HavenRuntime` after a successful dry-run |
| `pyproject.toml` parse errors during install | invalid TOML | Fix the file; do not `pip install` from a broken spec |

## 16. Updating / Redeploying

1. Change application code or `aws/agentcore/HavenRuntime/agentcore/agentcore.json`.
2. If you change runtime env vars, mirror them in
   `aws/agentcore/config/haven-runtime.env` (a test requires they match).
3. `pytest` and `ruff check src aws tests scripts` from the repo root.
4. `cd aws\agentcore\HavenRuntime`
5. `agentcore validate`
6. `agentcore deploy --dry-run --target dev`
7. `agentcore deploy --target dev`
8. `agentcore status`
9. Refresh the Cognito access token and rerun `pytest -m aws_live`

## 17. Security Notes

- Never commit `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`, Cognito passwords, access tokens, or refresh
  tokens.
- `.env`, `.env.*`, `.venv/`, `node_modules/`, and
  `aws/agentcore/HavenRuntime/agentcore/cdk/cdk.out/` are ignored.
- `.env.example` documents variable names only.
- Live tokens stay in the shell (`HAVEN_AGENTCORE_BEARER_TOKEN`).
- The runtime authenticates to AWS with its execution role, not with
  baked-in keys.
- The AgentCore container build context excludes `.env*`, `apps/`,
  tests, and the AgentCore project directory (`.dockerignore`).
