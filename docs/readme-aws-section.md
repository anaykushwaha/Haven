# README AWS section (Person A → Person B)

The root `README.md` is owned by Person B and is not present in the
repository as of this writing. Paste the section below into the root
README when it exists. Do not treat this file as a substitute README.

---

## AWS Architecture

Haven's production path is a hosted MCP server, not a direct
Alexa → LLM call:

```text
Alexa+
   ↓  MCP (Streamable HTTP) + Cognito JWT
Amazon Bedrock AgentCore Runtime (HavenMCP)
   ↓
Haven MCP server
   ├── context resolver
   ├── deterministic constraints
   ├── Strands + Amazon Bedrock planning
   ├── policy enforcement
   ├── deterministic execution (simulated household)
   ├── verification
   └── MemoryService → Amazon DynamoDB
```

Planning and execution are separate. Bedrock only selects among
candidates that application code already accepted. It does not
manipulate household devices.

### Services

- **Amazon Bedrock AgentCore** — public MCP runtime, Container build,
  CUSTOM_JWT authorizer. Entrypoint: `aws/agentcore/runtime.py`.
- **Amazon Bedrock** — planner reasoning via Strands structured output
  when `HAVEN_PLANNER_BACKEND=bedrock`.
- **Amazon DynamoDB** — `HavenPreferences`, `HavenActivities`,
  `HavenPlans`. Application code uses repositories, not raw table APIs.
- **Amazon Cognito** — issues the access tokens AgentCore validates.
- **IAM / Amazon CloudWatch** — scoped runtime policies for Bedrock,
  DynamoDB, and AgentCore runtime logs.

Haven does not use AgentCore Memory. Durable memory is Haven's own
`MemoryService`.

### Local vs Production

| | Local | Production |
| --- | --- | --- |
| Planner | `HAVEN_PLANNER_BACKEND=mock` | `bedrock` |
| Persistence | `HAVEN_PERSISTENCE_BACKEND=memory` | `dynamodb` |
| Process | `python scripts/run_server.py` | AgentCore `HavenMCP` |
| Auth | none on the local script | CUSTOM_JWT |

Defaults stay offline. AWS credentials never flip the backends.

### Deployment

See [docs/deployment.md](deployment.md) for the exact PowerShell
commands. The AgentCore project is `aws/agentcore/HavenRuntime/`.
Deploy with `agentcore deploy --target dev` from that directory after
`agentcore deploy --dry-run --target dev`. Architecture detail:
[docs/architecture.md](architecture.md). Service-by-service AWS notes:
[docs/aws.md](aws.md).
