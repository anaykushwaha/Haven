# Testing Haven

Haven is tested in layers, cheapest first. Everything except the last two
layers runs **offline** — mock planner, in-memory persistence, simulated home
— so `pytest` needs no AWS account, no Alexa+ and no network.

```bash
pip install -e ".[dev]"
pytest                    # the whole offline suite (aws_live tests are skipped)
ruff check src aws simulator tests scripts
```

## The layers

| # | Layer | What it proves | Run it |
|---|---|---|---|
| 1 | **Unit** — `tests/unit/` | Each module on its own: context resolution, planning constraints and policies, memory, the executor, the verifier and recovery, the home/security integrations, the dashboard API, the DynamoDB repositories | `pytest tests/unit` |
| 2 | **Simulator** — fixtures in `tests/fixtures/` | The simulated home behaves like a device hub: commands change state, offline devices refuse them, one shared device store | covered by `test_home_security_integration.py` |
| 3 | **Workflow** — `tests/workflows/` | A whole goal, end to end: context → plan → (confirm) → execute → verify, including failures and recovery | `pytest tests/workflows` |
| 4 | **MCP** — `tests/integration/test_mcp_flow.py` | The real server's tools against a real runtime: confirmation gating, clarification, retries, plans shared between instances, and (`anyio` tests) the wire protocol | `pytest tests/integration/test_mcp_flow.py -v` |
| 5 | **AgentCore** — `tests/integration/test_aws_flow.py` | Haven's AgentCore entrypoint over a real MCP client, in process | `pytest tests/integration/test_aws_flow.py -v` |
| 6 | **Live AWS** — `aws_live` marker | The *deployed* runtime, Bedrock and DynamoDB, with real credentials | see below |
| 7 | **Alexa+** — manual | Amazon's own language understanding and tool selection | `apps/alexa/README.md` |

Layers 1–5 are the regression net. Layers 6–7 are the only places Haven
touches the outside world, and are checked by hand before a release.

### The workflows

| Workflow | Test file | Highlights |
|---|---|---|
| Movie night | `tests/workflows/test_movie_night.py` | Picks a film that fits the free time, queues it, dims the lights; no confirmation |
| Leaving home | `tests/workflows/test_leaving_home.py` | Eight steps; locks and arming wait for a "yes"; nothing changes until then; offline door → partial result; a lock that ignores the command is caught and corrected |
| Tonight | `tests/workflows/test_tonight_planning.py` | Suggestions only; nothing in the home changes |

### Failure scenarios worth knowing

These are the cases a demo is most likely to hit, and each has a test:

| Scenario | Where | Expected |
|---|---|---|
| Device offline at planning time | `test_leaving_home.py` | Left out of the plan |
| Device drops offline after planning | `test_leaving_home.py`, `test_mcp_flow.py` | Retried once, then reported: `PARTIAL`, the rest still runs |
| Device ignores a command (claims success, doesn't change) | `test_verifier.py`, `test_leaving_home.py` | Verification says `MISMATCH`; one recovery pass fixes it, or reports it |
| No unconfirmed high-impact execution | `test_executor.py`, `test_mcp_flow.py` | `AWAITING_CONFIRMATION`, nothing runs |
| Haven can't plan safely | `test_mcp_flow.py` | Clarification question, no steps, `NO_ACTION` on execute |
| Same plan executed twice | `test_mcp_flow.py` | Second call returns the earlier result; nothing re-run |
| Plan unknown to this instance | `test_mcp_flow.py` | "Propose the plan again" — or found via the shared plan table |
| Temporary state offered as memory | `test_memory.py`, `test_mcp_flow.py` | Rejected |

## Shared fixtures

`tests/fixtures/` is registered for the whole suite by `pytest_plugins` in
`tests/conftest.py`. Every fixture builds a **fresh** simulator per test, so
no test can see another's lights, locks or saved preferences.

| Fixture | Gives you |
|---|---|
| `household_sim`, `household` | A fresh household simulator, and its domain snapshot |
| `media_sim`, `media_service` | The media simulator and its integration |
| `memory_service`, `calendar` | In-memory memory; a calendar pinned to a fixed "now" (a 120-minute free window) |
| `context_sources`, `make_context(goal)` | The five approved sources; a resolved `HouseholdContext` |
| `plan_for(goal)` | Context resolved and planned with the offline mock planner |
| `app_context` | An isolated MCP `AppContext`, as the server builds it (resets the process-wide cache before and after) |

Builders in `tests/fixtures/plans.py` — `make_step`, `make_plan`,
`lock_front_door_plan`, `clarification_plan` — make hand-built plans, and
`FlakyHouseholdBackend` is a device backend whose devices silently ignore
writes (`drop_writes(device_id, n)`), for testing verification and recovery.

## Rehearse the demo

```bash
python scripts/verify_demo.py
```

Runs each beat of [the demo](demo.md) through the same tool functions
Alexa+ reaches, and checks what the demo claims: the plan, the confirmation
gate, what changed, what was verified, how a failure is reported. Run it
before you record. `--dump-snapshots DIR` also saves the dashboard's JSON at
each moment.

## Check the submission

```bash
python scripts/verify_submission.py            # FAIL on problems, WARN on loose ends
python scripts/verify_submission.py --strict   # warnings fail too
```

Checks required files, the Apache-2.0 licence, every relative Markdown link,
leftover placeholders, key material, that every tool is documented with a
schema, that the demo timeline fits three minutes, and that the friction log
is complete.

## The dashboard

```bash
cd apps/dashboard
npm install
npm run typecheck && npm run build
```

CI (`.github/workflows/lint.yml`) runs both. There are no browser tests; the
dashboard's data contract is covered by `tests/unit/test_dashboard.py`.

## Live AWS tests (opt-in)

`aws_live` tests hit the deployed runtime and are skipped unless:

```bash
export HAVEN_AWS_LIVE=1
export HAVEN_AGENTCORE_RUNTIME_URL=<runtime invocation URL>
export HAVEN_AGENTCORE_BEARER_TOKEN=<Cognito access token>   # never commit this
pytest -m aws_live -v
```

They also need AWS credentials in the standard chain for Bedrock and
DynamoDB (see `aws/agentcore/config/README.md`).

## Continuous integration

| Workflow | Runs |
|---|---|
| `.github/workflows/test.yml` | `ruff check src aws tests scripts` and `pytest` |
| `.github/workflows/lint.yml` | `ruff check` over everything including `simulator/`; the dashboard type-check and build; an advisory check that `apps/alexa/schemas/` matches `src/haven/mcp/schemas.py` |

Both are offline and credential-free.

## Writing a test

- Use `plan_for(goal)` or `make_context(goal)` rather than building contexts
  by hand, unless you are testing the planner in isolation (then use
  `movie_night_context` from `tests/conftest.py`).
- Assert on **what happened to the home** (`household_sim.get_device_state`),
  not only on the status Haven reported — that gap is exactly what
  verification exists to close.
- MCP tools are plain functions taking the runtime first
  (`execute_household_plan(app_context, plan_id, confirmed=True)`); you do
  not need the `mcp` package to test their logic.
- Keep tests offline. Anything that needs credentials belongs behind
  `@pytest.mark.aws_live`.
- Use `pytest.raises(..., match=...)` on the *message* — Haven's errors are
  written for the user, so they are part of the behaviour.

## Known gaps

- The `anyio` protocol tests in `test_mcp_flow.py` need the real `mcp`
  client, as does all of `test_aws_flow.py`; they are the first thing to
  check on a new SDK version.
- Alexa+'s own language understanding is only reachable through the
  Alexa+ simulator, by hand.
- The simulated home cannot model a real hub's timing, rate limits or
  partial outages.
