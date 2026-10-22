# Hackathon submission

Everything to paste into the submission form, kept in the repository so it
stays true to the code. Items marked `REPLACE_…` are facts only you have
(URLs, usernames); `python scripts/verify_submission.py` lists any still
unfilled.

## Basics

**Title:** Haven — a household agent for Alexa+

**Tagline:** Smart homes understand commands. Haven understands situations.

**Tracks:** Alexa+ (primary) · AWS Builder · Open Source

**Demo video:** REPLACE_WITH_VIDEO_URL (under three minutes; script in
[demo.md](demo.md))

**Repository:** REPLACE_WITH_REPOSITORY_URL

## Inspiration

A smart home still works one command at a time. Getting ready to leave is
eight of them — and none of them checks that the door actually locked. We
wanted to say the goal and have the home work out the rest: with a safety
check before anything it can't easily undo, and an honest report afterwards.

## What it does

Haven is an MCP server that turns Alexa+ into a household agent. Given a
goal, it works out the situation, proposes a structured plan, **asks before
locking a door or arming security**, runs the plan, **re-reads the home to
verify each step**, and retries once if a device didn't respond. If it can't
plan safely it asks a question instead of guessing.

Three workflows are built: *movie night* (fits a film to the free time,
queues it, dims the lights), *leaving home* (lights off, TV stopped, thermostat
to away, doors locked, security armed) and *tonight* (suggestions only). A
read-only dashboard shows the plan, each step's result, Haven's verification,
and why it made the plan.

## How we built it

- **Alexa+ → MCP → Haven.** Six MCP tools over Streamable HTTP; JSON Schemas
  and example utterances in `apps/alexa/`.
- **Context resolver** gathers only what the goal needs from approved
  sources, and asks when it can't.
- **Planner** — deterministic constraints first; Amazon Bedrock (via Strands
  Agents) chooses among what's left when enabled; an offline mock planner is
  the default.
- **Executor and verifier are plain code, never the model.** High-impact
  steps need explicit confirmation, enforced in the planner, the executor and
  the tool.
- **AWS:** Bedrock AgentCore hosts the runtime behind a Cognito JWT
  authorizer; DynamoDB holds preferences, activity and proposed plans;
  least-privilege IAM. See [aws.md](aws.md).
- **A simulated home** (lights, thermostat, locks, Fire TV session) behind small
  interfaces a real hub would replace, so every behaviour is testable offline.

## Challenges

- **A model must not be trusted to act.** Keeping the model to planning, and
  making execution and verification deterministic, is what made confirmation
  and failure handling testable.
- **"It said it worked" isn't proof.** We built a test device that silently
  ignores commands to prove verification catches it, and recovery fixes it.
- **Stateless hosting vs. a two-step flow.** Alexa+ proposes and executes in
  separate requests, so plans are shared through DynamoDB across instances.
- **Integration seams.** Two real bugs lived between phases (clarification
  questions invisible over MCP; "optional" tool arguments that were required).
  Both are in the [friction log](friction-log.md).

## Accomplishments

- Confirmation, verification and recovery are tested end to end, including
  partial failure, with the failure modes written down.
- Offline by default: the whole suite runs with no AWS account or network.
- `scripts/verify_demo.py` rehearses the demo and `scripts/verify_submission.py`
  checks this repository before release.
- Honest documentation: [privacy.md](privacy.md) says what Haven does *not* do.

## What we learned

Build the verification before the features. Make the safe path the only path
(the executor re-checks policy rather than trusting the caller). Test the
seams between people's work, not just each person's work.

## What's next

See the README's [What's next](../README.md#whats-next): confirm the Alexa+
add-on and token flow against a live account, real Ring/Fire TV integrations,
a "forget this" tool, per-person authorization for confirmations.

## Built with

Alexa+ · Model Context Protocol (Python SDK, FastMCP) · Amazon Bedrock
AgentCore · Amazon Bedrock · Strands Agents · Amazon DynamoDB · AWS IAM ·
Amazon Cognito · Python · pydantic · pytest · ruff · React · TypeScript · Vite

## Open Source challenge

| Field | Value |
|---|---|
| Repository URL | REPLACE_WITH_REPOSITORY_URL |
| GitHub username | REPLACE_WITH_GITHUB_USERNAME |
| Contribution URL | REPLACE_WITH_CONTRIBUTION_URL |
| Licence | Apache-2.0 ([LICENSE](../LICENSE)) |

**Explanation.** Haven is released under Apache-2.0 as a complete, runnable project: an MCP server, a simulated home, a dashboard,
tests, and tooling. It is documented to be reused: tools follow a written
checklist ([mcp.md](mcp.md#adding-or-changing-a-tool)), integrations sit behind
small interfaces, and the verification-and-recovery layer is independent of
smart-home vendors. The repository includes contribution guidelines, issue
and pull-request templates, and a friction log of what was hard.
Contribution window: REPLACE_WITH_DATE_RANGE.

## Product feedback

Per tool, API and SDK we used. The full detail is in the
[friction log](friction-log.md).

### Alexa+ (add-on / MCP tooling)

- **Worked:** MCP as the integration contract is a good fit: six flat,
  well-described tools were enough for three workflows.
- **Hard:** We could not confirm, against a live account, the add-on manifest
  format or how Alexa+ presents a Cognito token to a custom MCP server.
- **Would use again:** Yes, once those two are documented.

### Model Context Protocol Python SDK (FastMCP)

- **Worked:** The lifespan pattern for shared state, tool decoration, and
  Streamable HTTP made the server small. An in-process ASGI client made
  protocol-level tests possible without a network.
- **Hard:** The testing and introspection surface moved between what we read
  and what we ran; optional-argument behaviour is easy to get wrong (a
  parameter without a default is *required* for clients).
- **Would use again:** Yes.

### Amazon Bedrock AgentCore, Amazon Bedrock, Strands Agents, DynamoDB, Cognito, IAM

REPLACE_WITH_PERSON_A_FEEDBACK — what worked, what didn't, onboarding, and
whether you would use each again (the hackathon asks for this per service).
The material is in the friction log and in [aws.md](aws.md).

## Feature requests

- A documented schema and a local validator for Alexa+ add-on manifests that
  declare an MCP integration.
- A local or simulated way to test an MCP integration end to end (including
  authentication) without deploying.
- Guidance for multi-request flows on stateless hosts: how a "propose, then
  confirm" interaction should keep its state when requests may land on
  different instances.

## Friction logs

[docs/friction-log.md](friction-log.md) — entries in the required
format for the tools we used, with what we tried, what happened, what worked, what
didn't, and the workaround.
