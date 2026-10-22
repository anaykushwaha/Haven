"""Check that the repository is ready to submit.

Runs offline, reads files only, and changes nothing. It answers the questions
a reviewer asks in the first five minutes, and the ones that are easy to get
wrong at the last minute:

- Are the required files and documents present?
- Is the licence Apache-2.0?
- Does every relative link in the Markdown docs point at something real?
- Are there leftover placeholders, TODOs or secrets in what we ship?
- Do the docs agree with the code (tool names, schema files)?
- Does the demo script fit in three minutes?
- Does the friction log cover each technology, in the required format?

Usage:
    python scripts/verify_submission.py
    python scripts/verify_submission.py --strict     # warnings fail too
    python scripts/verify_submission.py --root PATH  # check another checkout

Exit status is 1 if any check FAILs (or, with --strict, WARNs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

REQUIRED_FILES = [
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "pyproject.toml",
    ".env.example",
    "docs/architecture.md",
    "docs/aws.md",
    "docs/deployment.md",
    "docs/mcp.md",
    "docs/privacy.md",
    "docs/testing.md",
    "docs/demo.md",
    "docs/submission.md",
    "docs/friction-log.md",
    "apps/alexa/README.md",
    "apps/dashboard/README.md",
]

# Placeholders that must not survive into what is submitted.
PLACEHOLDER_PATTERNS = [
    re.compile(r"REPLACE[_-][A-Z0-9_-]+"),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"<your-[a-z-]+>", re.IGNORECASE),
    re.compile(r"\[yyyy\]|\[name of copyright owner\]"),
]
PLACEHOLDER_FILES = ["README.md", "docs/submission.md", "docs/demo.md", "docs/mcp.md",
                     "docs/privacy.md", "docs/testing.md", "CONTRIBUTING.md"]

SECRET_PATTERNS = {
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "AWS secret key": re.compile(r"aws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{30,}", re.IGNORECASE),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "JSON web token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{40,}"),
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "venv", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", "build"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pyc", ".woff", ".woff2",
                   ".pdf", ".mp4", ".mov"}
FRICTION_FIELDS = [
    "Tool / API / SDK:",
    "What we were trying to do:",
    "What happened:",
    "What worked well:",
    "What didn't:",
    "Workaround (if any):",
    "Would we use it again?",
]
FRICTION_TECHNOLOGIES = ["Alexa+", "MCP", "Bedrock", "DynamoDB", "AgentCore", "Strands"]
MAX_DEMO_SECONDS = 180

_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_TIME_RANGE = re.compile(r"(\d+):(\d{2})\s*[–-]\s*(\d+):(\d{2})")


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    message: str


def _walk(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix.lower() not in BINARY_SUFFIXES:
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_required_files(root: Path) -> list[Finding]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        return [Finding(FAIL, "required files", f"missing: {', '.join(missing)}")]
    return [Finding(PASS, "required files", f"all {len(REQUIRED_FILES)} present")]


def check_license(root: Path) -> list[Finding]:
    path = root / "LICENSE"
    if not path.is_file():
        return [Finding(FAIL, "license", "LICENSE is missing")]
    text = _read(path)
    if "Apache License" in text and "Version 2.0" in text:
        return [Finding(PASS, "license", "Apache-2.0")]
    return [Finding(FAIL, "license", "LICENSE is not the Apache License, Version 2.0")]


def _links_in(text: str) -> list[str]:
    stripped = _INLINE_CODE.sub("", _FENCE.sub("", text))
    return [match.group(1) for match in _LINK.finditer(stripped)]


def check_markdown_links(root: Path) -> list[Finding]:
    broken: list[str] = []
    checked = 0
    for path in _walk(root):
        if path.suffix.lower() != ".md":
            continue
        for target in _links_in(_read(path)):
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#)", target, re.IGNORECASE):
                continue  # external URL, mailto:, or a same-page anchor
            checked += 1
            relative = target.split("#", 1)[0].split("?", 1)[0]
            if not relative:
                continue
            resolved = (root / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
            if not resolved.exists():
                broken.append(f"{path.relative_to(root)} -> {target}")
    if broken:
        shown = "; ".join(broken[:8]) + (f" (+{len(broken) - 8} more)" if len(broken) > 8 else "")
        return [Finding(FAIL, "markdown links", f"{len(broken)} broken: {shown}")]
    return [Finding(PASS, "markdown links", f"{checked} relative links resolve")]


def check_placeholders(root: Path) -> list[Finding]:
    found: list[str] = []
    for name in PLACEHOLDER_FILES:
        path = root / name
        if not path.is_file():
            continue
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if any(pattern.search(line) for pattern in PLACEHOLDER_PATTERNS):
                found.append(f"{name}:{number}")
    if found:
        shown = ", ".join(found[:10]) + (f" (+{len(found) - 10} more)" if len(found) > 10 else "")
        return [Finding(WARN, "placeholders", f"fill in before submitting: {shown}")]
    return [Finding(PASS, "placeholders", "none left in the submission docs")]


def check_secrets(root: Path) -> list[Finding]:
    hits: list[str] = []
    for path in _walk(root):
        if path.stat().st_size > 2_000_000:
            continue
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if "EXAMPLE" in line or "REPLACE" in line:
                continue
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root)}:{number} ({label})")
    findings = []
    if hits:
        shown = "; ".join(hits[:8]) + (f" (+{len(hits) - 8} more)" if len(hits) > 8 else "")
        findings.append(Finding(FAIL, "secrets", f"possible secret material: {shown}"))
    else:
        findings.append(Finding(PASS, "secrets", "no key material or tokens found"))
    if (root / ".env").exists():
        findings.append(Finding(WARN, "secrets", ".env exists here; make sure it is untracked (git ls-files .env)"))
    return findings


def check_tool_docs(root: Path) -> list[Finding]:
    catalog_path = root / "apps/alexa/config/tool_catalog.json"
    mcp_doc = root / "docs/mcp.md"
    if not catalog_path.is_file() or not mcp_doc.is_file():
        return [Finding(WARN, "tool docs", "tool_catalog.json or docs/mcp.md missing; skipped")]
    names = [tool["name"] for tool in json.loads(_read(catalog_path)).get("tools", [])]
    doc = _read(mcp_doc)
    problems = []
    for name in names:
        if name not in doc:
            problems.append(f"{name} not in docs/mcp.md")
        schema = root / "apps/alexa/schemas" / f"{name.removesuffix('_tool')}.schema.json"
        if not schema.is_file():
            problems.append(f"no schema file {schema.relative_to(root)}")
    if problems:
        return [Finding(FAIL, "tool docs", "; ".join(problems))]
    return [Finding(PASS, "tool docs", f"{len(names)} tools documented, each with a schema")]


def check_demo_length(root: Path) -> list[Finding]:
    path = root / "docs/demo.md"
    if not path.is_file():
        return [Finding(WARN, "demo length", "docs/demo.md missing; skipped")]
    ranges = [
        (int(a) * 60 + int(b), int(c) * 60 + int(d))
        for a, b, c, d in _TIME_RANGE.findall(_read(path))
    ]
    if not ranges:
        return [Finding(WARN, "demo length", "no `m:ss – m:ss` timeline found in docs/demo.md")]
    problems = []
    previous_end = 0
    for start, end in ranges:
        if end <= start:
            problems.append(f"range {start}s-{end}s runs backwards")
        if start < previous_end:
            problems.append(f"range starting at {start}s overlaps the previous one")
        previous_end = max(previous_end, end)
    if previous_end > MAX_DEMO_SECONDS:
        problems.append(f"runs {previous_end}s, over the {MAX_DEMO_SECONDS}s limit")
    if problems:
        return [Finding(FAIL, "demo length", "; ".join(problems))]
    return [Finding(PASS, "demo length", f"{previous_end}s of {MAX_DEMO_SECONDS}s")]


def check_friction_log(root: Path) -> list[Finding]:
    path = root / "docs/friction-log.md"
    if not path.is_file():
        return [Finding(WARN, "friction log", "docs/friction-log.md missing; skipped")]
    text = _read(path)
    entries = re.split(r"(?m)^### ", text)[1:]
    entries = [entry for entry in entries if not entry.startswith("[YYYY")]
    findings = []
    incomplete = [
        entry.splitlines()[0][:60]
        for entry in entries
        if any(field not in entry for field in FRICTION_FIELDS)
    ]
    if incomplete:
        findings.append(Finding(WARN, "friction log", f"entries missing a required field: {'; '.join(incomplete)}"))
    else:
        findings.append(Finding(PASS, "friction log", f"{len(entries)} entries, all complete"))
    uncovered = [tech for tech in FRICTION_TECHNOLOGIES if tech.lower() not in text.lower()]
    if uncovered:
        findings.append(Finding(WARN, "friction coverage", f"no entry mentions: {', '.join(uncovered)}"))
    else:
        findings.append(Finding(PASS, "friction coverage", "every technology is covered"))
    return findings


CHECKS: list[Callable[[Path], list[Finding]]] = [
    check_required_files,
    check_license,
    check_markdown_links,
    check_placeholders,
    check_secrets,
    check_tool_docs,
    check_demo_length,
    check_friction_log,
]


def run_checks(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the repository is ready to submit.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    args = parser.parse_args(argv)

    findings = run_checks(args.root)
    for finding in findings:
        print(f"{finding.level:<5} {finding.check}: {finding.message}")

    failures = sum(1 for f in findings if f.level == FAIL)
    warnings = sum(1 for f in findings if f.level == WARN)
    print(f"\n{failures} failed, {warnings} warnings, {len(findings) - failures - warnings} passed.")
    return 1 if failures or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
