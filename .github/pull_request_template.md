## What changed and why

<!-- One or two sentences. Link the issue if there is one. -->

## How it was tested

<!-- Commands you ran, e.g. `pytest`, `ruff check src aws simulator tests scripts`. -->

## Checklist

- [ ] Tests added or updated (assert on what happened to the home, not only on the status)
- [ ] Docs updated if behaviour changed (`docs/mcp.md`, `docs/privacy.md`, …)
- [ ] If an MCP tool changed: schemas re-exported (`python scripts/export_mcp_schemas.py`)
- [ ] No secrets, tokens or real household data
- [ ] Anything high-impact still needs confirmation, and has a verification check
