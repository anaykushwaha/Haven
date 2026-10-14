# Haven AgentCore deployment configuration

| File | Purpose |
| --- | --- |
| `haven-runtime.env` | Non-secret runtime environment for the deployed `HavenMCP` runtime. Mirrored into `envVars` in `../HavenRuntime/agentcore/agentcore.json` (the deploy source of truth); a test keeps them identical. |

## Runtime facts

| Item | Value |
| --- | --- |
| AgentCore project | `HavenRuntime` (`../HavenRuntime/agentcore/agentcore.json`, CDK-managed) |
| Runtime name | `HavenMCP` (deployed as `HavenRuntime_HavenMCP`) |
| Build | `Container` — `Dockerfile` at the repo root, built by CodeBuild for ARM64 |
| Entrypoint | `aws/agentcore/runtime.py` → `haven.mcp.server.create_server(host="0.0.0.0", port=8000, stateless_http=True)` |
| Protocol | `MCP` (Streamable HTTP on port 8000, path `/mcp`) |
| Network | `PUBLIC` |
| Authorizer | `CUSTOM_JWT` — Cognito user pool `us-west-2_rc81kxXsN`, allowed client `7u4n2us7e3if17i9325qakhisi` |
| Forwarded headers | `Authorization` only |
| Target | `dev` → account `110144245769`, `us-west-2` (`../HavenRuntime/agentcore/aws-targets.json`) |

## Why a container instead of CodeZip

The CLI's CodeZip packager installs dependencies into the zip root and copies
the repository tree next to them. The deployed zip root (`/var/task`) is what
Python searches, and Haven's application package lives in `src/haven`, so
`import haven` would fail at runtime. The container build runs
`pip install .` instead, which installs `haven`, `aws` and `simulator` the
same way `pip install -e .` does locally — no `sys.path` manipulation.

## IAM

The CDK-managed execution role already grants Bedrock model invocation,
X-Ray and CloudWatch Logs. Haven still attaches the three runtime policies
through `additionalPolicies` so Bedrock, CloudWatch and DynamoDB access are
explicit and reviewable:

- `aws/iam/policies/haven-bedrock-runtime.json`
- `aws/iam/policies/haven-cloudwatch-logs-runtime.json`
- `aws/iam/policies/haven-dynamodb-runtime.json`

Table provisioning (`haven-dynamodb-provisioning.json`) is a separate policy
for the operator running `scripts/setup_dynamodb.py`; the runtime never
creates tables.

## Secrets

None are stored in this repository. The runtime uses its execution role for
AWS access; callers present a Cognito JWT as `Authorization: Bearer <token>`.
For live tests export `HAVEN_AGENTCORE_BEARER_TOKEN` in your shell only.
