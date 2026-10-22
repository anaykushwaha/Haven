# Privacy

Haven runs a household, so it is built to know as little about it as it can
and still do the job. This page says what it collects, what it keeps, what
it deliberately does not do, and where the honest gaps are. Every claim
below points at the code that makes it true.

**The design rule:** Haven does not try to know everything happening in the
home. It gathers the *minimum context needed for the goal in front of it*,
asks the user when it cannot get something reliably, and remembers only what
was deliberately worth remembering.

## Camera-free by default

Haven has no camera, microphone, audio or video code anywhere in the
repository, and nothing in its context model could carry such data. Presence
and occupancy come only from simple facts: something the user said ("Sam is
staying behind"), a household record, or a low-resolution sensor reading such
as `room_occupied = true`. `src/haven/models/room.py` says it plainly: Haven
never stores camera or video data. Voice is Alexa+'s domain: Haven only ever
receives the text of a goal and tool arguments.

## Minimal context, from approved sources only

`resolve_context` (`src/haven/context/resolver.py`) builds a snapshot **for
one goal**, from a short, fixed list of sources
(`src/haven/context/sources.py`):

| Source | What it can tell Haven |
|---|---|
| User statement | Something said in this conversation ("I have 45 minutes") |
| Connected service | Calendar free time, from a service the user connected |
| Device state | Whether the TV is on, a door is locked, a light is on |
| Low-resolution sensor | A boolean such as `room_occupied` — never video |
| Saved memory | Preferences and history Haven was told to keep |

There is no continuous monitoring: nothing polls the home in the background,
and context is resolved when a tool is called. The planner receives a
deliberately small contract (`src/haven/models/context.py`: people and
presence, rooms, devices and their capabilities, media titles, free minutes,
saved preferences), not raw logs or readings.

## Context expires

Facts gathered during a request carry how long they should be trusted
(`src/haven/context/expiry.py`):

| Kind | Examples | Trusted for |
|---|---|---|
| Instant | needed only for this request | not reused |
| Transient | room occupancy, who is home, device and lock state | 2 minutes |
| Session | calendar free time | 30 minutes |
| Durable | saved preferences and history | until changed |

Each request resolves context afresh and the fact store is scoped to one
resolution, so transient facts are never carried into later requests or
written anywhere; the expiry rules stop a stale reading being reused *within*
a resolution. The dashboard, when enabled, re-reads state on every poll
rather than caching it.

## What is remembered, and what is not

`MemoryService` (`src/haven/memory/service.py`) stores three things, and the
MCP server stores a fourth when cloud persistence is on:

| Stored | Contents | Example |
|---|---|---|
| **Preferences** | key, value, household or person scope | `preferred_genres = ["sci-fi", "comedy"]` |
| **Activity history** | a type, a one-line summary, and small details (text, number or boolean only) | "Movie night prepared"; the film's title |
| **Plan history** | plan id, workflow, step count, whether it needed confirmation, and a summary line containing the goal as phrased | "Proposed plan for: We're leaving the house" |
| **Proposed plans** (cloud only) | the whole plan: goal, each step's title, description and device id. **Not** execution results | Lets a plan proposed by one server instance be confirmed on another |

Changes to preferences also leave a small audit entry.

What is **refused**, in code, not by convention: temporary state can never
become memory. Presence, occupancy, lock state, device state, and similar
keys are rejected both as preferences and as activity details
(`TRANSIENT_KEYS` in `src/haven/memory/preferences.py`) with errors such as
*"'room_occupied' describes temporary state and cannot be saved as memory"*.
Clarification plans (a question with no steps) are not written to history.

What Haven does **not** store: audio or transcripts, images or video, raw
sensor readings, who was home at a given time, lock or alarm state, the
calendar's contents, or device credentials.

**Be aware:** the goal text *is* kept inside a plan-history summary line, and
a plan's id and workflow with it (and, with cloud persistence, the full
plan in `HavenPlans`). That is the phrase Alexa+ passed to Haven,
not a recording of the user's voice, but it can reveal when someone asked to
secure the house.

## Ask, don't guess

When Haven cannot get an important fact reliably, it asks:

- The resolver raises `ClarificationNeeded` for a required fact no approved
  source can answer, rather than inventing a value.
- The planner returns a **clarification plan** (a question, no steps) when it
  cannot plan safely, and `execute_household_plan_tool` then runs nothing and
  repeats the question.
- Unknown presence stays *unknown*: it is not assumed to be "home" or "away".
  The leaving-home plan even says so on the arm step — *"2 person(s)
  currently appear to be home; confirm everyone has left first."*
- Anything that locks a door or arms security needs explicit confirmation
  (see [mcp.md](mcp.md#confirmation-and-safety)).

## Where data lives

| Where | What | Notes |
|---|---|---|
| Local run | In process memory (default) | Gone when the server stops |
| AWS | DynamoDB: `HavenPreferences`, `HavenActivities`, `HavenPlans` | On-demand tables; AWS encrypts DynamoDB data at rest by default. Access is limited by the IAM policies in `aws/iam/policies/` |
| Server memory | The plans proposed since start (newest 200) and their results | Contains steps and device ids; execution results are never persisted |
| Logs | Plan ids, device ids, statuses, errors | Written by Haven's loggers to the process output (CloudWatch in AgentCore); no names, audio or readings |
| Dashboard | A live snapshot, in the browser | Opt-in, read-only; household and people names are shown |

## Access and exposure

- The **deployed runtime** sits behind a Cognito JWT authorizer: requests
  without a valid token never reach Haven.
- A **local server has no authentication** (Haven's own code does none).
  Use it on a private machine or tunnel, and don't expose it broadly.
- The **dashboard API is off unless you pass `--dashboard-api`**, only
  answers `GET`/`HEAD`, and exposes who is home and which doors are locked, so
  never enable it on a tunnelled port with a real household. The one exception
  is the recorded demo with the simulated household, described in
  [demo.md](demo.md#demo-state-and-the-dashboards-exposure)
  ([`apps/dashboard/README.md`](../apps/dashboard/README.md)).
- Secrets are never committed: AWS access uses the standard credential chain,
  `.env` is ignored, and `scripts/verify_submission.py` scans for key
  material before release.

## What Haven does not do

Being honest about the limits matters more than a tidy list of promises:

- **It does not identify the speaker.** `confirmed=true` is set by the client
  (Alexa+); Haven trusts that the user really agreed. Anyone who can reach
  the server's tools could pass it. There is no per-person authorization yet.
- **It models one household.** There are no user accounts and no
  multi-household isolation. The demo household is a simulation.
- **There is no "forget this" tool.** Preferences can be deleted in code
  (`MemoryService.delete_preference`) and tables can be emptied by an AWS
  administrator, but a user cannot yet ask Alexa+ to forget something, and
  stored history has no automatic expiry (no DynamoDB TTL).
- **The home is simulated.** The privacy properties of real smart-home
  integrations (Ring, Hue, a thermostat's cloud) are theirs and Amazon's, not
  something Haven can promise.
- **Alexa+'s own handling of voice and the tool calls it makes** is governed
  by Amazon's policies, not by this repository.

## Reporting a problem

Open an issue (see [CONTRIBUTING.md](../CONTRIBUTING.md)), and never include
real credentials, tokens, or household details in it.
