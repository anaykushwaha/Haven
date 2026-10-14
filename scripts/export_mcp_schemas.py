"""Export Haven's MCP request/response schemas as JSON Schema files.

Reads the pydantic models in `haven.mcp.schemas` (the single source of
truth) and writes one `<tool_name>.schema.json` per tool into
`apps/alexa/schemas/`, each with a `request` and `response` key. Run
this whenever a tool's parameters or response shape change, so
`apps/alexa/schemas/` never drifts from the actual server.

Usage:
    python scripts/export_mcp_schemas.py
    python scripts/export_mcp_schemas.py --check   # exit 1 if files would change

Requires the package to be installed (`pip install -e .`), since this
imports real pydantic models and calls their real `model_json_schema()`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from haven.mcp import schemas as mcp_schemas

REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "apps" / "alexa" / "schemas"

# tool_name -> (request model, response model)
TOOL_SCHEMAS: dict[str, tuple[type, type]] = {
    "get_household_context": (
        mcp_schemas.GetHouseholdContextRequest,
        mcp_schemas.HouseholdContextSummary,
    ),
    "propose_household_plan": (
        mcp_schemas.ProposeHouseholdPlanRequest,
        mcp_schemas.PlanSummary,
    ),
    "get_plan_status": (
        mcp_schemas.GetPlanStatusRequest,
        mcp_schemas.PlanStatusSummary,
    ),
    "execute_household_plan": (
        mcp_schemas.ExecuteHouseholdPlanRequest,
        mcp_schemas.ExecutionSummary,
    ),
    "save_household_preference": (
        mcp_schemas.SaveHouseholdPreferenceRequest,
        mcp_schemas.PreferenceSummary,
    ),
    "get_activity_recommendations": (
        mcp_schemas.GetActivityRecommendationsRequest,
        mcp_schemas.RecommendationsResponse,
    ),
}


def build_schema_documents() -> dict[str, dict[str, Any]]:
    """Build the `{tool_name: {"request": ..., "response": ...}}` mapping."""

    documents: dict[str, dict[str, Any]] = {}
    for tool_name, (request_model, response_model) in TOOL_SCHEMAS.items():
        documents[tool_name] = {
            "tool": tool_name,
            "request": request_model.model_json_schema(),
            "response": response_model.model_json_schema(),
        }
    return documents


def write_schema_files(documents: dict[str, dict[str, Any]], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for tool_name, document in documents.items():
        path = output_dir / f"{tool_name}.schema.json"
        text = json.dumps(document, indent=2, sort_keys=False) + "\n"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check_up_to_date(documents: dict[str, dict[str, Any]], output_dir: Path) -> bool:
    for tool_name, document in documents.items():
        path = output_dir / f"{tool_name}.schema.json"
        expected = json.dumps(document, indent=2, sort_keys=False) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write anything; exit 1 if apps/alexa/schemas/ is out of date.",
    )
    args = parser.parse_args()

    documents = build_schema_documents()

    if args.check:
        if check_up_to_date(documents, OUTPUT_DIR):
            print("apps/alexa/schemas/ is up to date.")
            return 0
        print("apps/alexa/schemas/ is out of date — run without --check to regenerate.")
        return 1

    written = write_schema_files(documents, OUTPUT_DIR)
    for path in written:
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
