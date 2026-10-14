# Haven IAM policies

Least-privilege policy documents. Runtime access and provisioning are kept
separate so the deployed agent can never create or delete tables.

| Policy | Attached to | Grants |
| --- | --- | --- |
| `haven-dynamodb-runtime.json` | AgentCore `HavenMCP` execution role (`additionalPolicies`) | Item read/write/query on the three Haven tables only |
| `haven-bedrock-runtime.json` | AgentCore `HavenMCP` execution role (`additionalPolicies`; CDK also grants an equivalent statement) | `InvokeModel` / `InvokeModelWithResponseStream` on foundation models and inference profiles |
| `haven-cloudwatch-logs-runtime.json` | AgentCore `HavenMCP` execution role (`additionalPolicies`; CDK also grants an equivalent statement) | Log group/stream creation and `PutLogEvents` under `/aws/bedrock-agentcore/runtimes/*` |
| `haven-dynamodb-provisioning.json` | Operator / CI identity running `scripts/setup_dynamodb.py` | `CreateTable` / `DescribeTable` on the Haven tables, `ListTables` |

Rules enforced by `tests/integration/test_aws_flow.py`:

- no statement uses `"Action": "*"` or `"Action": "dynamodb:*"` style wildcards;
- runtime policies never include table lifecycle actions (`CreateTable`, `DeleteTable`, `UpdateTable`);
- no policy document embeds an access key, secret, or token.
