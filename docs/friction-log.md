# Haven Friction Log

This log tracks friction, surprises, and rough edges encountered while
building Haven with Alexa+, MCP, and AWS. Add an entry as soon as you hit
something worth noting — don't wait until submission week.

The hackathon submission requires product feedback for every tool, API,
or SDK used. This log is the raw material for that write-up, so be
specific: what you tried, what happened, and what you'd change.

## How to Add an Entry

Copy the template below into a new section, dated, with your initials.

```
### [YYYY-MM-DD] — Short title (Initials)

**Tool / API / SDK:**

**What we were trying to do:**

**What happened:**

**What worked well:**

**What didn't:**

**Workaround (if any):**

**Would we use it again?**
```

## Entries

### [Phase 8] — The dashboard had two bugs only a screenshot showed (B)

**Tool / API / SDK:** The React dashboard (`apps/dashboard`), headless Chromium

**What we were trying to do:** Produce the README and demo screenshots from the
real dashboard components and real backend data.

**What happened:** Looking at the first render found two problems that the
type-check and the 24 render assertions had both passed: after a plan ran
and was verified, its badge still read "Awaiting confirmation" (the
*proposal's* status), and long rows in the Timeline overlapped their labels.

**What worked well:** Rendering the real components with `react-dom/server`
and real snapshots from `scripts/verify_demo.py --dump-snapshots` made the
screenshots reproducible, and the same data drove the assertions.

**What didn't:** Assertions on text content can't see layout or a misleading
label. Nothing automated covers the dashboard's appearance.

**Workaround (if any):** The plan header now shows what *happened* once it
has run (execution and verification status) and the proposal status only
before; rows wrap long text. The screenshots are rendered server-side, not
captured from a live Vite session.

**Would we use it again?** Yes. Look at the screenshots before a release; add
browser tests if the dashboard grows.

### [Phase 8] — A two-step flow on a stateless host needed shared state (B)

**Tool / API / SDK:** Bedrock AgentCore Runtime (stateless HTTP), `HavenPlans`

**What we were trying to do:** Make "propose a plan" and "execute it", which
Alexa+ sends as separate requests, work on the deployed runtime.

**What happened:** Plans were kept only in each server process, and the
AgentCore runtime is stateless: a second request may reach a different
instance or session, where the plan id would be unknown. Person A had already
built `DynamoDBPlanRepository` (full plan payload, and IAM for it) but
nothing used it.

**What worked well:** The repository and policy already existed, so the fix
was small: `PlanStore` writes plans through to it and reloads on a miss, and
degrades to in-process memory if DynamoDB is unreachable. Covered by tests
with a fake shared repository, including the failure case.

**What didn't:** Not run against real DynamoDB or the deployed runtime. Only
the *plan* is shared: a plan executed on one instance still reports as ready
to execute on another (re-running is safe; every step is idempotent). Storing
full plans in the cloud also stores the goal text and device ids, which
`docs/privacy.md` now says.

**Workaround (if any):** Unknown plans return "Ask Haven to propose the plan
again." Two related fixes in the same pass: executing an already-completed,
verified plan returns the earlier result instead of re-running it, and the
in-process store is bounded to 200 plans.

**Would we use it again?** Yes — and verify it live: with credentials, propose
a plan, then execute it from a second connection, against the deployed runtime.

### [Phase 7] — Our own docs described authentication that wasn't true (B)

**Tool / API / SDK:** Cognito JWT authorizer on AgentCore; Alexa+ add-on tooling

**What we were trying to do:** Write the final MCP, privacy and Alexa+ docs
from what the code and deployment actually do.

**What happened:** Reading the deployment config showed the runtime sits behind
a Cognito `CUSTOM_JWT` authorizer, while `apps/alexa/README.md` and
`.env.example` still said Haven had no authentication and described a
placeholder token for a server that "would implement bearer auth" — and the
README pointed at an "Authentication" section that did not exist.

**What worked well:** Writing the privacy page claim by claim, each tied to a
file, is what surfaced it.

**What didn't:** How Alexa+ would obtain and present that Cognito token to a
custom MCP server is still unconfirmed against a live account, so end-to-end
Alexa+ → deployed runtime is the one unverified link.

**Workaround (if any):** The Alexa docs now separate "local server: no auth"
from "deployed runtime: Cognito JWT", say the Alexa+ token flow is an open
question, and never put a token in the repository.

**Would we use it again?** Yes — a check that docs agree with config. The first
thing to do with an Alexa+ account is settle the token flow.

### [Phase 6] — Nothing in this phase could be run against the real toolchain (B)

**Tool / API / SDK:** `ruff`, `pytest` + `pytest-anyio`, the real `mcp` client, `npm`/`vite`

**What we were trying to do:** Land the Phase 5/6 code with every test and
lint rule green.

**What happened:** This environment has no network, so none of those could
be installed. Instead: (1) the Python tests ran under small local
stand-ins for `pydantic`, `pytest` and `mcp` (type validation included, so
passing `None` where a `str` is declared fails as it would for real);
(2) Python lint rules (unused imports/variables, `zip(strict=)`, import
ordering, f-strings) were checked with hand-written AST scripts, not `ruff`;
(3) the dashboard was type-checked with `tsc` against minimal React type
stubs and rendered with `react-dom/server` using real backend JSON, but
never built with `vite`.

**What worked well:** Everything that does not need the network is
covered: 337 backend tests pass here, and the dashboard's data contract was
checked against the *real* snapshot the Python code produces.

**What didn't:** Four protocol-level tests in
`tests/integration/test_mcp_flow.py` (marked `anyio`) need the real `mcp`
client and were written to mirror Person A's passing tests in
`test_aws_flow.py`, not run. Two older tests
(`test_mock_planner_import_does_not_load_aws_modules`,
`test_mock_planning_runs_offline_without_aws_modules`) spawn a clean
subprocess and need real `pydantic`.

**Workaround (if any):** None — this is the first thing to do with network
access: `pip install -e ".[dev]" && ruff check src aws simulator tests scripts && pytest`,
then `cd apps/dashboard && npm install && npm run build`.

**Would we use it again?** Yes, but treat the first real CI run as the
actual verification, and read any failure there as likely a small
real-vs-stand-in difference rather than a design problem.

### [Phase 5] — Clarification plans were invisible over MCP; optional tool arguments were required (B)

**Tool / API / SDK:** Internal contract — `haven.mcp.schemas`, `haven.mcp.tools.*`

**What we were trying to do:** Run Person A's Phase 5 planner end to end
through the MCP tools.

**What happened:** Two seams were broken. (1) The planner now answers an
unplannable goal with a *clarification plan* (no steps) instead of
raising, but `PlanSummary` had no field for the question, so Alexa+ would
have received an empty plan and never asked the user anything; executing it
reported a misleading "Nothing completed successfully". (2) `confirmed`,
`person_id` and `goal` were declared without defaults, so a client that
omitted them got a validation error even though the docs called them
optional.

**What worked well:** Both were caught by reading the planner's new
behaviour against the MCP layer rather than by a failing test, then pinned
by tests: unit/direct tests for the clarification fields and a protocol
test asserting the optional arguments are absent from each tool's
`required` list.

**What didn't:** Nothing about the design — it was an integration gap
between two people's phases.

**Workaround (if any):** `PlanSummary.clarification_needed` /
`clarification_prompt`, `ExecutionStatus.NO_ACTION`, and real defaults on
the three tool parameters. `apps/alexa/schemas/propose_household_plan.schema.json`
was regenerated; the other five schema files are unchanged.

**Would we use it again?** Yes. Worth a standing rule: any change to
`haven.models.plan` also needs a look at `haven.mcp.schemas`.

### [Phase 5] — The dashboard reads household state, so its API is opt-in and read-only (B)

**Tool / API / SDK:** `FastMCP.streamable_http_app()` (ASGI), `uvicorn`

**What we were trying to do:** Give the React dashboard live data from the
same process Alexa+ is driving, without adding an MCP tool (Alexa+ would
see it) or a second copy of the household.

**What happened:** `haven/mcp/dashboard.py` adds a small pure-ASGI wrapper
that serves `GET /api/dashboard` and `GET /api/health` next to `/mcp` and
forwards everything else (including lifespan events) untouched.
`scripts/run_server.py --dashboard-api` starts it. It is off by default
because the MCP port is often tunnelled to the public internet for Alexa+,
and that endpoint exposes who is home and which doors are locked. It only
accepts GET/HEAD.

**What worked well:** No Starlette dependency, and the wrapper is tested
with plain ASGI calls.

**What didn't:** Not run against a real `uvicorn` + `streamable_http_app()`
here. The AgentCore container does not include it (its image copies only
`src`, `aws`, `simulator`), so the dashboard only watches a local server —
a dashboard for the deployed runtime would need an authenticated endpoint.

**Workaround (if any):** For a demo, run the server locally with
`--dashboard-api` and point the tunnel at a *second* server started
without it.

**Would we use it again?** Yes for local demos. Do not enable it on a
tunnelled port.

### [Phase 4] — apps/alexa/schemas/ generated with a local schema-export shim, not real pydantic (B)

**Tool / API / SDK:** `pydantic` (`BaseModel.model_json_schema()`)

**What we were trying to do:** Run `scripts/export_mcp_schemas.py` to
generate `apps/alexa/schemas/*.schema.json` from the real
`haven.mcp.schemas` pydantic models, so those files are accurate rather
than hand-typed and liable to drift.

**What happened:** No network access to install real `pydantic` here
either, so the shipped `apps/alexa/schemas/*.schema.json` files were
generated using a small local `model_json_schema()` implementation
(added to the same test-only pydantic shim used since Phase 2). Field
names, types, required/optional status, descriptions, and nesting are
all read from the real `haven.mcp.schemas` models and should be
accurate; the exact JSON Schema draft conventions (`$defs`/`$ref` for
nested models vs. full inlining, exact `anyOf`/`default` phrasing) will
likely look slightly different once regenerated with real pydantic.

**What worked well:** Because `scripts/export_mcp_schemas.py` imports
`haven.mcp.schemas` directly and calls `.model_json_schema()` rather than
hand-duplicating field lists, the *content* (what's required, what types,
what descriptions) doesn't depend on which pydantic generated it — only
the exact on-disk JSON shape might shift slightly.

**What didn't:** Nothing wrong, just unconfirmed cosmetic drift.

**Workaround (if any):** None needed — this is what the script is for.

**Would we use it again?** Yes. Run `python scripts/export_mcp_schemas.py`
once real pydantic is installed (`pip install -e .`) and commit whatever
changes — `python scripts/export_mcp_schemas.py --check` exits 1 if the
checked-in files are stale, so this is easy to wire into CI later
(Phase 6).

### [Phase 4] — Alexa+ add-on manifest schema not confirmed against a live account (B)

**Tool / API / SDK:** Alexa+ developer console / add-on tooling

**What we were trying to do:** Produce `apps/alexa/config/addon.json` in
whatever shape the Alexa+ console actually imports or expects for
registering a custom MCP-backed add-on, plus store-listing content
(name, description, example phrases, privacy links).

**What happened:** No live Alexa+ developer account was available while
building this. `addon.json` is modeled on Amazon's existing, well
documented Alexa Skills Kit skill manifest format (`manifestVersion`,
`storeListing.locales`, `privacyAndCompliance`, `mediaAssets`) with an
`integrations` block added for the MCP endpoint, since that's the
closest confirmed precedent for "a JSON manifest describing an Alexa
add-on" — not a confirmed Alexa+ add-on schema.

**What worked well:** Reusing a real, stable format (ASK's skill
manifest) for everything except the new `integrations` block means most
of the file is low-risk even if Alexa+'s add-on schema differs — store
listing fields in particular are unlikely to have changed shape.

**What didn't:** Nothing to report yet — this needs a real account to
actually test. The `integrations` block specifically is invented and
has no precedent to lean on.

**Workaround (if any):** `apps/alexa/README.md`'s "Local development
setup" section walks through manually entering these values into the
console step by step, rather than assuming `addon.json` imports as-is.

**Would we use it again?** Too early to say. First thing to do once
someone on the team has Alexa+ console access: confirm whether it has a
manifest import at all, and if so, update `addon.json` to match that
shape exactly — the `storeListing` section should carry over easily;
the `integrations` block is the part most likely to need rework.

### [Phase 4] — mcp SDK's FastMCP introspection/call surface, still unconfirmed (B)

**Tool / API / SDK:** `mcp` (the official MCP Python SDK),
`mcp.server.fastmcp.FastMCP`

**What we were trying to do:** Extend Phase 3's `haven/mcp/server.py`
usage with a genuine protocol-level check in
`tests/integration/test_mcp_flow.py` — calling `FastMCP.call_tool(name,
arguments)` directly, the way a real MCP request eventually would.

**What happened:** Still no network access to install the real `mcp`
package in this environment (see the Phase 3 entry below — same root
cause). `test_call_tool_via_fastmcp_protocol_surface` is written to
`pytest.skip(...)` if `call_tool` doesn't exist on the installed
`FastMCP`, or if its signature/behavior doesn't match what's assumed
here, specifically so an SDK version mismatch can't fail the suite.
Against the local test shim (which has no `call_tool` at all) it
skips cleanly, as designed — confirmed, but obviously not the same as
confirming it works against the real package.

**What worked well:** Structuring every other test in the file to call
`haven.mcp.tools.*` functions directly, through a real `AppContext` built
from the server's own `app_lifespan`, means the suite's actual
correctness guarantees don't depend on `call_tool` at all. That layer
covers the full context → plan → execute → verify → recommend pipeline
and passes regardless of this uncertainty.

**What didn't:** Nothing concrete — by design, this is deferred risk,
not a known failure.

**Workaround (if any):** The skip-on-mismatch pattern in
`test_call_tool_via_fastmcp_protocol_surface`.

**Would we use it again?** Yes. First thing to check once `pip install
-e .` succeeds with network access: run `pytest
tests/integration/test_mcp_flow.py -v` and see whether that one test
actually runs or skips. If it skips, read the skip reason — it'll say
exactly what differed — and either fix the call or accept the skip and
rely on the MCP Inspector (`apps/alexa/README.md`) for real
protocol-level confidence instead.

**Status (Phase 7):** Largely resolved. `FastMCP(..., lifespan, host, port, stateless_http)` and Streamable HTTP at `/mcp` are exercised by a real MCP client in `tests/integration/test_aws_flow.py`; the `call_tool` experiment was replaced by in-process protocol tests in `tests/integration/test_mcp_flow.py`, which follow the same pattern but have not been confirmed on a real install.

### [Phase 3] — mcp SDK usage not verifiable offline (B)

**Tool / API / SDK:** `mcp` (the official MCP Python SDK), specifically
`mcp.server.fastmcp.FastMCP`

**What we were trying to do:** Build `haven/mcp/server.py` against the
documented FastMCP lifespan pattern: a `@dataclass AppContext`, an
`@asynccontextmanager async def app_lifespan(server) -> AsyncIterator[AppContext]`
passed to `FastMCP(name, lifespan=...)`, tools reading shared state via
`ctx.request_context.lifespan_context`, and `server.run(transport="streamable-http")`
for the Streamable HTTP transport Alexa+ requires.

**What happened:** This dev environment has no network access, so `mcp`
couldn't be `pip install`-ed to test against the real package. Everything
in `haven/mcp/` was built and verified against a small local stand-in
(matching just this usage) instead: `FastMCP(name, instructions=,
lifespan=, host=, port=)`, `@mcp.tool()`, `Context.request_context.lifespan_context`,
`server.run(transport=)`. All 6 tools were registered and called through
that stand-in — including the confirmation-gating path on a synthetic
high-impact plan — and behaved correctly.

**What worked well:** Keeping each tool's actual logic (the functions
`haven.mcp.tools.*` export alongside `register()`) as plain,
dependency-injected functions with no import of `mcp` at all meant the
business logic itself could be fully tested regardless. Only the
`register()` wrapper and the `Context`/`FastMCP` imports depend on the
real package.

**What didn't:** The exact FastMCP constructor kwargs (`host=`, `port=`
passed directly vs. set via `server.settings`) and the precise
`transport=` string for Streamable HTTP could plausibly differ by
installed version. This wasn't independently confirmed against a real
`mcp` install.

**Workaround (if any):** None needed yet — but this is the first thing
to check the moment `pip install -e .` succeeds with network access:
`python scripts/run_server.py` should start cleanly, and
`npx @modelcontextprotocol/inspector` (or curling the Streamable HTTP
endpoint) should list all 6 tools. If the constructor signature has
moved, it's isolated to `create_server()` in `haven/mcp/server.py` —
nothing else touches FastMCP directly.

**Would we use it again?** Yes, but flag this as the first thing to
smoke-test locally before wiring up the Alexa+ web simulator (Phase 4) —
don't assume this is 100% confirmed to work in the real environment yet.

**Status (Phase 7):** Resolved for the server wiring: the lifespan pattern, tool registration and Streamable HTTP are exercised against the real SDK by `tests/integration/test_aws_flow.py`. The original note stays as a record of why it was uncertain.

### [Phase 2] — PlanStep has no structured media_id (B)

**Tool / API / SDK:** Internal contract — `haven.models.plan.PlanStep`

**What we were trying to do:** Have the executor's `prepare_media_session`
and `select_media` handlers read which title the planner selected
directly off the `PlanStep`, the same way they read `device_id` and
`room_id`.

**What happened:** `PlanStep` (owned by Person A) has no `media_id`
field, and `candidate_to_step` doesn't carry `ActionCandidate.media_id`
through to the step either — it's dropped at that boundary. The selected
title only survives in the free-text `title` / `description` strings
(e.g. `"Select 'Paddington 2' (103 min) ..."`).

**What worked well:** The phrasing is consistent enough (always a single
quoted title) that a small regex plus a title lookup against the media
catalog (`MediaService.find_media_id_by_title`) recovers it reliably for
the mock reasoner's output.

**What didn't:** This is fragile — it breaks if the phrasing in
`agents/planner.py` or the Bedrock reasoner's summary ever changes, and
it silently fails (a `FAILED` step, not a crash) if a title doesn't
match exactly.

**Workaround (if any):** `extract_quoted_title()` +
`MediaService.find_media_id_by_title()` in
`haven/execution/actions.py`, documented inline as a deliberate,
narrow bridge rather than a general parser.

**Would we use it again?** No — flagging for Person A: adding an
optional `media_id: str | None = None` field to `PlanStep` (and
threading `ActionCandidate.media_id` through `candidate_to_step`) would
remove the need for this entirely and make execution far more robust.
Worth doing before Phase 3 wires this up to MCP.

---

_Add new entries above this line, most recent first._
