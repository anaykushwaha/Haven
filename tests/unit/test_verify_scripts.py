"""Tests for the two pre-release scripts: verify_demo.py and verify_submission.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses (with postponed annotations) looks the module up by name.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_demo = _load("verify_demo")
verify_submission = _load("verify_submission")


# --------------------------------------------------------------------------- #
# verify_demo
# --------------------------------------------------------------------------- #


def test_every_demo_beat_passes():
    results = verify_demo.run_demo()

    assert [beat.number for beat in results] == [1, 2, 3, 4, 5, 6]
    for beat in results:
        assert beat.error is None, beat.error
        assert beat.checks, f"beat {beat.number} checked nothing"
        failed = [c.description for c in beat.checks if not c.passed]
        assert not failed, f"beat {beat.number} ({beat.title}): {failed}"


def test_demo_beats_match_the_documented_script():
    titles = [title for _, title, _ in verify_demo.BEATS]
    demo_doc = (SCRIPTS.parent / "docs" / "demo.md").read_text()

    # The scenes the doc promises are the ones being rehearsed.
    for phrase in ("movie night", "leaving home", "goes wrong", "privacy"):
        assert phrase in demo_doc.lower()
    assert any("Movie night" in t for t in titles)
    assert any("Leaving home" in t for t in titles)


def test_demo_dumps_dashboard_snapshots(tmp_path):
    verify_demo.run_demo(tmp_path)

    names = sorted(path.name for path in tmp_path.glob("*.json"))
    assert names == [
        "01-idle.json",
        "02-movie-night.json",
        "03-leaving-awaiting-confirmation.json",
        "04-leaving-verified.json",
        "05-partial-failure.json",
        "06-clarification.json",
    ]
    verified = json.loads((tmp_path / "04-leaving-verified.json").read_text())
    assert verified["active_plan"]["verification_status"] == "VERIFIED"
    partial = json.loads((tmp_path / "05-partial-failure.json").read_text())
    assert partial["active_plan"]["execution_status"] == "PARTIAL"


def test_a_drifting_beat_is_reported_not_hidden(monkeypatch):
    def broken(self):
        self.expect("this can never hold", False, "on purpose")

    monkeypatch.setattr(verify_demo.Rehearsal, "beat_tonight", broken)

    results = verify_demo.run_demo()

    assert results[0].passed is False
    assert results[0].checks[0].detail == "on purpose"
    assert all(beat.passed for beat in results[1:])
    assert verify_demo.main([]) == 1


def test_a_crashing_beat_is_reported_and_the_rest_still_run(monkeypatch):
    def crash(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(verify_demo.Rehearsal, "beat_preference", crash)

    results = verify_demo.run_demo()

    assert results[1].error == "RuntimeError: boom"
    assert results[1].passed is False
    assert all(beat.passed for beat in results if beat.number != 2)


def test_demo_output_names_every_beat():
    lines = []
    verify_demo._print(verify_demo.run_demo(), lines.append)
    text = "\n".join(lines)

    assert "Beat 1:" in text and "Beat 6:" in text
    assert "6 of 6 beats passed" in text


# --------------------------------------------------------------------------- #
# verify_submission
# --------------------------------------------------------------------------- #


def _repo(tmp_path: Path, **overrides: str) -> Path:
    """A minimal repository that passes every check; override files to break it."""

    files = {
        "README.md": "# Haven\nSee [mcp](docs/mcp.md).\n",
        "LICENSE": "Apache License\nVersion 2.0, January 2004\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "pyproject.toml": "[project]\n",
        ".env.example": "X=1\n",
        "docs/mcp.md": "get_thing_tool\n",
        "docs/demo.md": "| 0:00 – 0:30 | a |\n| 0:30 – 2:00 | b |\n",
        "docs/friction-log.md": (
            "## Entries\n\n### [Phase 1] — t (B)\n\n"
            + "\n".join(f"**{f}** x" for f in verify_submission.FRICTION_FIELDS)
            + "\nAlexa+ MCP Bedrock DynamoDB AgentCore Strands\n"
        ),
        "apps/alexa/config/tool_catalog.json": json.dumps({"tools": [{"name": "get_thing_tool"}]}),
        "apps/alexa/schemas/get_thing.schema.json": "{}",
    }
    for name in verify_submission.REQUIRED_FILES:
        files.setdefault(name, "x\n")
    files.update(overrides)
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _levels(root: Path) -> dict[str, list[str]]:
    levels: dict[str, list[str]] = {}
    for finding in verify_submission.run_checks(root):
        levels.setdefault(finding.check, []).append(finding.level)
    return levels


def test_a_complete_repo_passes_everything(tmp_path):
    root = _repo(tmp_path)

    assert {lv for levels in _levels(root).values() for lv in levels} == {"PASS"}
    assert verify_submission.main(["--root", str(root)]) == 0


def test_missing_required_files_fail(tmp_path):
    root = _repo(tmp_path)
    (root / "docs/privacy.md").unlink()

    findings = verify_submission.check_required_files(root)

    assert findings[0].level == "FAIL" and "docs/privacy.md" in findings[0].message


def test_the_wrong_license_fails(tmp_path):
    root = _repo(tmp_path, LICENSE="MIT License\n")

    assert verify_submission.check_license(root)[0].level == "FAIL"


def test_broken_relative_links_fail_but_external_and_code_links_do_not(tmp_path):
    root = _repo(
        tmp_path,
        **{
            "README.md": (
                "[ok](docs/mcp.md) [web](https://example.com) [anchor](#top) "
                "[gone](docs/nope.md) `[inline](docs/inline-nope.md)`\n"
                "```\n[fenced](docs/fenced-nope.md)\n```\n"
            )
        },
    )

    finding = verify_submission.check_markdown_links(root)[0]

    assert finding.level == "FAIL"
    assert "docs/nope.md" in finding.message
    assert "inline-nope" not in finding.message and "fenced-nope" not in finding.message


def test_links_with_anchors_resolve_to_their_file(tmp_path):
    root = _repo(tmp_path, **{"README.md": "[x](docs/mcp.md#section)\n"})

    assert verify_submission.check_markdown_links(root)[0].level == "PASS"


@pytest.mark.parametrize("line", ["TODO: finish this", "see REPLACE_WITH_URL", "[yyyy] owner", "<your-username>"])
def test_placeholders_warn(tmp_path, line):
    root = _repo(tmp_path, **{"docs/demo.md": f"| 0:00 – 1:00 | a |\n{line}\n"})

    assert verify_submission.check_placeholders(root)[0].level == "WARN"


def test_secrets_fail(tmp_path):
    key = "AKIA" + "A" * 16  # built at run time so this file never contains a full key
    root = _repo(tmp_path, **{"docs/notes.txt": f"aws key {key}\n"})

    finding = verify_submission.check_secrets(root)[0]

    assert finding.level == "FAIL" and "AWS access key id" in finding.message


def test_example_values_are_not_secrets(tmp_path):
    key = "AKIA" + "A" * 16
    root = _repo(tmp_path, **{"docs/notes.txt": f"EXAMPLE {key}\n"})

    assert verify_submission.check_secrets(root)[0].level == "PASS"


def test_an_env_file_warns(tmp_path):
    root = _repo(tmp_path, **{".env": "A=1\n"})

    levels = [f.level for f in verify_submission.check_secrets(root)]

    assert levels == ["PASS", "WARN"]


def test_undocumented_tools_and_missing_schemas_fail(tmp_path):
    root = _repo(
        tmp_path,
        **{"apps/alexa/config/tool_catalog.json": json.dumps({"tools": [{"name": "other_tool"}]})},
    )

    finding = verify_submission.check_tool_docs(root)[0]

    assert finding.level == "FAIL"
    assert "other_tool not in docs/mcp.md" in finding.message
    assert "other.schema.json" in finding.message


@pytest.mark.parametrize(
    ("timeline", "level"),
    [
        ("| 0:00 – 2:50 | a |\n", "PASS"),
        ("| 0:00 – 3:00 | a |\n", "PASS"),
        ("| 0:00 – 3:01 | a |\n", "FAIL"),
        ("| 0:00 – 1:00 | a |\n| 0:50 – 2:00 | b |\n", "FAIL"),
        ("| 1:00 – 0:30 | a |\n", "FAIL"),
        ("no times here\n", "WARN"),
    ],
)
def test_demo_length(tmp_path, timeline, level):
    root = _repo(tmp_path, **{"docs/demo.md": timeline})

    assert verify_submission.check_demo_length(root)[0].level == level


def test_friction_log_needs_complete_entries_and_every_technology(tmp_path):
    root = _repo(
        tmp_path,
        **{"docs/friction-log.md": "### [Phase 1] — thin entry (B)\n\n**What happened:** x\n"},
    )

    levels = [f.level for f in verify_submission.check_friction_log(root)]

    assert levels == ["WARN", "WARN"]


def test_strict_mode_fails_on_warnings(tmp_path):
    root = _repo(tmp_path, **{"docs/demo.md": "no times here\n"})

    assert verify_submission.main(["--root", str(root)]) == 0
    assert verify_submission.main(["--root", str(root), "--strict"]) == 1


def test_a_failure_exits_nonzero(tmp_path):
    root = _repo(tmp_path, LICENSE="MIT\n")

    assert verify_submission.main(["--root", str(root)]) == 1
