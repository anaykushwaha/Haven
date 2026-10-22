# Contributing to Haven

Thanks for your interest. Haven is a household agent for Alexa+, built as an
MCP server; this page is how to get set up, how the code is organised, and
how to change it safely. Contributions are accepted under the project's
[Apache-2.0 licence](LICENSE).

## Getting started

Requires Python 3.11+ (and Node 20+ only if you touch the dashboard).

```bash
git clone <your fork>
cd haven
pip install -e ".[dev]"
cp .env.example .env            # optional; everything defaults to offline

pytest                          # the full offline suite — no AWS, no network
ruff check src aws simulator tests scripts
python scripts/run_movie_night.py   # a narrated end-to-end run
python scripts/verify_demo.py       # rehearses every demo beat
```

Dashboard: `cd apps/dashboard && npm install && npm run typecheck`.

## How the code is organised

| Path | What lives there |
|---|---|
| `src/haven/context/` | Context resolver: approved sources, facts, expiry |
| `src/haven/planning/`, `src/haven/agents/` | Constraints, workflow recognition, planner, policies, Bedrock reasoner |
| `src/haven/execution/`, `src/haven/verification/` | Deterministic action handlers, the executor, the verifier, recovery |
| `src/haven/integrations/` | Calendar, media, home, security — each behind a small interface |
| `src/haven/memory/` | Preferences and activity (in memory or DynamoDB) |
| `src/haven/mcp/` | The MCP server, tools, schemas, dashboard API |
| `aws/` | Bedrock client, DynamoDB tables, IAM policies, AgentCore runtime |
| `simulator/` | The simulated home |
| `apps/alexa/`, `apps/dashboard/` | Alexa+ config and schemas; the React dashboard |
| `tests/` | `unit/`, `workflows/`, `integration/`, `fixtures/` — see [docs/testing.md](docs/testing.md) |

Read [docs/mcp.md](docs/mcp.md) for the tool interface and
[docs/privacy.md](docs/privacy.md) before changing what Haven collects or
remembers.

## Principles that code review enforces

- **The model plans; code acts.** Execution and verification never call a
  model. Constraints that code can check (time, availability, capabilities)
  must not be left to a model to reason about.
- **Never silently do something irreversible.** Locking and arming are
  `HIGH_IMPACT_ACTION`s and need confirmation, re-checked by the executor.
- **Verify, don't assume.** A new action that changes the home needs a
  matching check in `haven/verification/verifier.py`.
- **Ask, don't guess.** If a needed fact can't be obtained reliably, ask.
- **Temporary state is never memory.** Presence, occupancy, device and lock
  state are rejected as preferences and activity details.
- **Offline by default.** Anything that needs credentials goes behind
  `@pytest.mark.aws_live`.

## Style

- Python, type-hinted, `from __future__ import annotations`, pydantic models
  at boundaries, a short docstring saying *why* a module exists.
- `ruff` is the linter and import sorter (`pyproject.toml`); CI runs it.
- Comments explain intent and trade-offs, not what the next line does.

## Common changes

**Add an MCP tool** — follow the checklist in
[docs/mcp.md](docs/mcp.md#adding-or-changing-a-tool): plain function,
`register`, schemas, `export_mcp_schemas.py`, `tool_catalog.json`, tests.

**Add an action** (something a plan step can do):

1. A handler in `src/haven/execution/actions.py`, registered in `_HANDLERS`,
   using an integration (`home`, `security`, `media`) rather than touching
   device state directly. Report failures with the `ERR_*` codes.
2. A check in `src/haven/verification/verifier.py`.
3. Tests in `tests/unit/test_executor.py` and `test_verifier.py`.

**Add a workflow** — recognise it in `src/haven/planning/workflows.py`, plan it
in the planner (with a risk label per step), then add the action handlers and
a file in `tests/workflows/`.

**Change a model** — if you touch `haven.models.plan`, check
`haven/mcp/schemas.py` too. A missed field once made clarification questions
invisible to Alexa+ ([friction log](docs/friction-log.md)).

## Tests

Use the shared fixtures (`plan_for`, `make_context`, `app_context`, …) and
assert on **what happened to the home**, not only on the status Haven
reported. Details and the failure scenarios already covered are in
[docs/testing.md](docs/testing.md).

## Pull requests

1. Open an issue first for anything sizeable.
2. Branch from `main`; keep the change small and focused.
3. Add or update tests, and docs if behaviour changed.
4. Run `pytest`, `ruff check src aws simulator tests scripts`, and, before a
   release, `python scripts/verify_submission.py`.
5. Describe *what changed and why*; the template will prompt you.

## Never commit

Secrets, tokens, AWS credentials, `.env`, or real household data. The demo
household is a simulation with made-up names; keep it that way.
`scripts/verify_submission.py` scans for key material.

## Friction log

If a tool, SDK or process was confusing, slow or broken, add an entry to
[docs/friction-log.md](docs/friction-log.md) as you go, in its format. It is
the raw material for product feedback.

## Reporting a problem

Use the issue templates. For anything security- or privacy-sensitive, don't
post real tokens or household details; describe the behaviour instead.
