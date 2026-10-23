# Haven AWS Demo Runbook

Person A's operational checklist for recording or presenting the AWS
side of Haven. Keep this next to the laptop. Do not open pages that
show tokens, passwords, or account billing.

## Before Recording

1. **AWS login**
   ```powershell
   aws login --profile haven-dev
   $env:AWS_PROFILE="haven-dev"
   $env:AWS_REGION="us-west-2"
   aws sts get-caller-identity
   ```
   If this fails, stop. Do not record a "production" AWS demo.

2. **AgentCore status**
   ```powershell
   cd aws\agentcore\HavenRuntime
   agentcore status
   ```
   Need a healthy `HavenMCP` runtime in `us-west-2`. If
   `deployed-state.json` is still `{"targets": {}}`, the current
   configuration has not been deployed.

3. **Bedrock availability**
   ```powershell
   python scripts\test_bedrock_connections.py
   ```
   This is a tiny Strands → Bedrock ping. A failure here means
   production planning will fail. Do not silently switch the recorded
   demo to mock and call it AWS production.

4. **DynamoDB tables**
   ```powershell
   aws dynamodb list-tables --region us-west-2
   ```
   Expect `HavenPreferences`, `HavenActivities`, `HavenPlans`.

5. **Fresh Cognito token**
   Obtain a new access token through the normal Cognito flow. Export
   it in the shell only:
   ```powershell
   $env:HAVEN_AGENTCORE_BEARER_TOKEN="<temporary access token>"
   $env:HAVEN_AGENTCORE_RUNTIME_URL="<runtime invocation URL>"
   $env:HAVEN_AWS_LIVE="1"
   ```

6. **Live smoke test**
   ```powershell
   pytest -m aws_live
   ```
   Minimum remote checks if the full marker is too slow:
   MCP initialize, `tools/list`, `get_household_context_tool`, then one
   `propose_household_plan_tool` ("Get movie night ready.").
   Do not execute high-impact steps.

If any of the above is BLOCKED, say so in the recording. Mock mode is
fine for a *local* debugging walkthrough, not for an AWS production
claim.

## Demo Architecture Talking Points

Use this spoken explanation (about 60 seconds):

> Haven is not Alexa talking to an LLM. Alexa+ calls Haven over MCP.
> We host that MCP server on Amazon Bedrock AgentCore so the public
> endpoint is a real MCP runtime with Cognito JWT authentication —
> AgentCore checks the bearer token before Haven ever runs. Inside the
> container is the same Haven server we develop locally. Haven first
> resolves only the context that goal needs, then generates action
> candidates and applies deterministic constraints. Only those accepted
> candidates go to Strands on Amazon Bedrock. The model returns IDs,
> not devices or risk classes; application code builds the Plan. That
> plan does not touch the house. Execution is a later, policy-checked
> step against a simulated household runtime, then verification reads
> the result. Durable preferences and activity history go through
> Haven's MemoryService into DynamoDB. IAM splits runtime item access
> from table provisioning. The model decides what makes sense;
> deterministic code decides what is allowed.

## Production Verification

See `docs/aws-production-verification.md` for the dated evidence log.
Before recording, confirm that file still matches what `agentcore
status` and `pytest -m aws_live` say today.

## Demo Sequence

1. Show an Alexa+ or local MCP request: "Get movie night ready."
2. Say that Alexa+ calls Haven over MCP, not Bedrock directly.
3. Show AgentCore as the hosted MCP runtime (`HavenMCP`, protocol MCP,
   Container, CUSTOM_JWT). A status line is enough; skip IAM consoles.
4. Show the planning request reaching Haven (`propose_household_plan_tool`).
5. Explain Strands + Bedrock structured planning: candidates in, IDs
   out, application-built steps.
6. Show the resulting plan in the dashboard if Person B has it running
   locally. The dashboard is a demo layer, not the production path.
7. Show a saved preference or a `PLAN_PROPOSED` activity in Haven.
8. Explain that production persistence is DynamoDB via repositories.
9. Close on JWT + IAM: the public endpoint requires a Cognito token;
   the runtime role cannot create tables and cannot be called with
   long-lived keys baked into the image.

Do not unlock doors, disable security, or run other high-impact
actions. The household is a simulated demo environment.

## What to Show in AWS

Useful, low-risk:

- AgentCore runtime name / region / protocol (from `agentcore status`)
- The three DynamoDB table names (not item contents with personal data)
- CloudWatch log group existence for the runtime, if already verified

Skip unless they add to the story:

- Cognito user pages
- IAM policy JSON
- raw JWT authorizer traces
- billing

## Failure Recovery

| Failure | Check | Honest move |
| --- | --- | --- |
| Expired AWS login | `aws sts get-caller-identity` | `aws login --profile haven-dev`. Do not continue a production claim. |
| Expired Cognito token | remote 401/403 | Get a fresh access token. Do not reuse a token from a file. |
| AgentCore runtime unhealthy | `agentcore status` | Do not edit `deployed-state.json`. Redeploy only if you have a valid session and a successful `agentcore deploy --dry-run --target dev`. |
| Bedrock access failure | `ModelInvocationError` category, or `test_bedrock_connections.py` | Record BLOCKED. Local mock is for debugging only. |
| DynamoDB permission / missing table | `PersistenceError` or `ResourceNotFoundException` | `python scripts/setup_dynamodb.py` with the operator identity; do not attach provisioning rights to the runtime role. |

Do not add fake fallbacks that make production look healthy.

## After Demo

- Unset `HAVEN_AGENTCORE_BEARER_TOKEN` from the shell.
- Do not commit `.env`, tokens, screenshots of JWTs, or CloudWatch
  lines that contain household detail.
- If anything failed live, update
  `docs/aws-production-verification.md` with PASS / FAIL / BLOCKED /
  NOT RUN. Do not backfill success.
