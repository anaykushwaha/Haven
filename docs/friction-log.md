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
