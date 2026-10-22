# Haven dashboard

A read-only view of what Haven is doing, so you (or a judge) can watch
Alexa+ drive the agent: the household, the plan Haven proposed, how each
step went, whether Haven *verified* the home actually changed, and why it
made the plan.

It is a visualization and debugging layer, not the product. It never sends
a command — it only reads.

## Run it

Two terminals, from the repo root:

```bash
# 1. Haven's MCP server, with the dashboard's read-only API switched on
python scripts/run_server.py --dashboard-api

# 2. The dashboard
cd apps/dashboard
npm install
npm run dev          # http://localhost:5173
```

Then talk to Haven the usual way (Alexa+ via a tunnel, the MCP Inspector,
or the scripts). Try, in order:

1. "Get movie night ready" — a reversible plan; it runs without asking.
2. "We're leaving the house" — a plan with high-impact steps (locking,
   arming); watch it sit at **Awaiting confirmation** until you say yes.
3. "Go ahead" — every step flips to Succeeded and the Verification panel
   fills in as Haven re-reads the doors, lights and thermostat.

## What each panel shows

| Panel | Source |
|---|---|
| **Active plan** | The most recently proposed plan, each step's status, retries |
| **Why Haven made this plan** | Plain sentences built from the plan and current context — never a fresh model call, so it can't disagree with what ran |
| **Verification results** | Haven's re-check of the home after acting (Verified / Mismatch / Skipped) |
| **Household** | Who is home, rooms, and the facts used for planning |
| **Simulator state** | Every simulated device: Fire TV session, lights, thermostat, locks, security |
| **Timeline** | Recent plans and the activity Haven chose to remember |

## How it works

```
Browser --/api/dashboard--> Vite dev proxy --> Haven server (same process as /mcp)
```

`haven/mcp/dashboard.py` builds one JSON snapshot from the live server
state and serves it at `GET /api/dashboard`. The dashboard polls it every
two seconds; a failed poll keeps the last good data on screen, marked
stale, and recovers on its own.

`src/types/dashboard.ts` mirrors that Python module by hand — when
`DashboardSnapshot` changes, update it to match.

## Privacy

The snapshot says who is home and which doors are locked. So the API is
**off unless you pass `--dashboard-api`** (or set `HAVEN_DASHBOARD_API=1`),
only answers GET/HEAD, and should never be enabled on a server whose port
is tunnelled to the internet for Alexa+.

The one exception is a recorded demo with the simulated household: the
dashboard has to run in the *same process* Alexa+ drives, so the tunnel exposes
it too. That is fine only because the household is made up — use a throwaway
tunnel URL, stop the server afterwards, and see [`docs/demo.md`](../../docs/demo.md#demo-state-and-the-dashboards-exposure).

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the `/api` proxy |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run build` | Type-check, then a production build into `dist/` |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VITE_HAVEN_PROXY_TARGET` | `http://127.0.0.1:8787` | Where the dev proxy sends `/api` |
| `VITE_HAVEN_API_URL` | *(empty)* | Call a server directly instead of via the proxy (then the server needs CORS, which Haven does not add) |

## Known limits

- It only watches a *local* server. The AgentCore container does not
  include the dashboard API; watching the deployed runtime would need an
  authenticated endpoint.
- The simulator can't be changed from the dashboard (by design: it is
  read-only), so failure demos need code — see
  `tests/workflows/test_leaving_home.py` for the scenarios.
- No `package-lock.json` is committed yet. After your first
  `npm install`, commit it and switch `.github/workflows/lint.yml` to
  `npm ci`.
