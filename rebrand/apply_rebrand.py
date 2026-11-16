#!/usr/bin/env python3
"""Apply the CASTADOO MCP rebrand overlay to a pristine upstream checkout.

This script is the ONLY source of truth for the rebrand. The branch
``rebrand/CASTADOO_MCP`` is never hand-edited: it is always
``upstream/main`` + the output of this script, in one commit. Upstream syncs
are therefore a reset+regenerate rather than a merge, so merge conflicts
cannot happen by construction.

Design rules
------------
1. Deterministic — same input tree gives a byte-identical output tree. The
   verifier regenerates in a throwaway worktree and compares git tree hashes.
2. Fail loud — required anchors are asserted. If upstream renames a file or
   rewrites a snippet, this exits non-zero naming the anchor instead of
   producing a half-rebranded tree. Fix the rule here, never on the branch.
3. Idempotent — a second run changes nothing.
4. Minimal blast radius — Python package name, environment variables, tool
   names, prompt names and resource URIs are deliberately untouched.

Stage order (each stage depends on the previous one's invariants):

    S1 delete upstream-only paths (marketing site, publish workflows)
    S2 regex identity pass over upstream text files
    S3 surgical per-file edits that regex cannot express safely
       (pyproject, uv.lock, manifests, CLAUDE.md)
    S4 promo removal, Markdown only (line deletion must never touch JSON/YAML)
    S5 authored whole-file replacements (README head, LICENSE, NOTICE, ...)
    S6 generated CI workflow + MANIFEST.in augmentation
    S7 residual scan — any surviving promo token in a structured file aborts

Authored files are written in S5 and are excluded from S2/S4, otherwise the
promo stripper would delete NOTICE.md's own attribution links.

Usage
-----
    python rebrand/apply_rebrand.py [--repo PATH]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

GENERATED_MARKER = "[generated-rebrand]"

# ---------------------------------------------------------------------------
# Identity — the only place these strings are defined.
# ---------------------------------------------------------------------------

FORK_OWNER = "castaso"
FORK_REPO = "CASTADOO_MCP"
FORK_URL = f"https://github.com/{FORK_OWNER}/{FORK_REPO}"
UPSTREAM_URL = "https://github.com/erpipe-org/mcp-odoo"

DIST_NAME = "castadoo-mcp"
REGISTRY_ID = "io.github.castaso/castadoo-mcp"
IMAGE = "ghcr.io/castaso/castadoo-mcp"
DISPLAY_NAME = "CASTADOO MCP"
DISPLAY_NAME_SERVER = "CASTADOO MCP Server"
HOLDER = "CASTADOO"
YEAR = "2026"

DESCRIPTION = (
    f"{DISPLAY_NAME} for AI agents - 41 tools, gated writes, multi-instance. "
    f"Fork of odoo-mcp (MIT)."
)
KEYWORDS = ('["odoo", "mcp", "server", "model-context-protocol", '
            '"chatgpt", "claude"]')

TEXT_SUFFIXES = {
    ".md", ".py", ".toml", ".json", ".yml", ".yaml", ".html", ".txt",
    ".cfg", ".ini", ".mdc", ".example", ".in", ".sh", ".ps1", ".lock",
}

# Written wholesale in S5. Never touched by S2/S3/S4.
AUTHORED = {
    "LICENSE",
    "NOTICE.md",
    "TRADEMARKS.md",
    "SYNCING.md",
    "CLAUDE.md",
}

# Rewritten by dedicated stage-3 functions after the identity pass has run over
# them; only README/CHANGELOG are excluded from the blanket passes because they
# are replaced wholesale from assets.
DEDICATED = {
    "README.md", "CHANGELOG.md",
}

# Tooling must never rewrite itself.
TOOLING = {
    "rebrand/apply_rebrand.py", "rebrand/verify.py",
    "rebrand/hook_pre_push.py", "rebrand/sync_upstream.py",
    "rebrand/hooks/pre-push", "rebrand/assets/README.head.md",
}

SKIP_ALL = AUTHORED | DEDICATED | TOOLING | {"GATES.md", "PLAN.md"}

DELETE_PATHS = [
    "site",                                       # upstream marketing deck
    "docs/comparison.md",                         # competitor/hosted marketing
    "docs/catalog-submission.md",                 # upstream registry submission notes
    ".github/workflows/docker-publish.yml",        # publishes upstream's image
    ".github/workflows/mcp-registry.yml",          # publishes upstream's registry id
    ".github/workflows/pages.yml",                 # publishes upstream's Pages site
    ".github/FUNDING.yml",                         # upstream maintainer's funding
]

# README lines that only exist to point at deleted docs.
README_DROP_LINES = [
    "Comparing specific projects?",
    "| [docs/comparison.md]",
]

# ---------------------------------------------------------------------------
# S2 identity rules. `minimum` > 0 means "must match somewhere or abort".
# Order matters: composite identifiers must be rewritten before bare tokens.
# ---------------------------------------------------------------------------

IDENTITY_RULES: list[tuple[str, str, int]] = [
    (r"io\.github\.erpipe-org/mcp-odoo", REGISTRY_ID, 1),
    (r"ghcr\.io/erpipe-org/mcp-odoo", IMAGE, 1),
    (r"https://github\.com/erpipe-org/mcp-odoo", FORK_URL, 0),
    (r"github\.com/erpipe-org/mcp-odoo", f"github.com/{FORK_OWNER}/{FORK_REPO}", 0),
    (r"erpipe-org/mcp-odoo", f"{FORK_OWNER}/{FORK_REPO}", 0),
    (r"erpipe-org\.github\.io/mcp-odoo/?", f"{FORK_URL}#readme", 0),
    (r"https://skills\.sh/(?:b/)?erpipe-org/mcp-odoo",
     f"https://skills.sh/{FORK_OWNER}/{FORK_REPO}", 0),
    (r"\bodoo-mcp==__VERSION__\b", f"{DIST_NAME}==__VERSION__", 0),
    (r"=== ODOO MCP SERVER STARTING ===",
     f"=== {DISPLAY_NAME.upper()} SERVER STARTING ===", 1),
    (r'"Odoo MCP Server"', f'"{DISPLAY_NAME_SERVER}"', 1),
    (r"Odoo MCP setup", f"{DISPLAY_NAME} setup", 0),
    (r"\bOdoo MCP\b", DISPLAY_NAME, 0),
    (r"\bodoo-mcp\b", DIST_NAME, 1),
]

# Matches the hosted-product promo. Only applied to Markdown, line by line.
PROMO = re.compile(r"erpipe", re.IGNORECASE)

# Files allowed to mention upstream by name for legal/provenance reasons.
PROMO_ALLOWED = {"NOTICE.md", "LICENSE", "CHANGELOG.md", "TRADEMARKS.md"}

# Exact lines that legitimately carry an upstream URL for provenance. These are
# deliberately narrow: they must match the whole trimmed line, so a stray promo
# sentence in the same file still fails the residual scan.
ALLOWED_PROVENANCE_LINES = {
    'Upstream = "https://github.com/erpipe-org/mcp-odoo"',
    "Upstream = \"https://github.com/erpipe-org/mcp-odoo\"",
}


def die(msg: str) -> None:
    print(f"apply_rebrand: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        die(f"{path} is not valid UTF-8; add it to AUTHORED or extend SKIP_ALL")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" is what makes the tree hash platform-independent.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def is_text(rel: str, path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name in {
        "LICENSE", "Dockerfile", "MANIFEST.in", ".gitignore",
        ".dockerignore", ".editorconfig", ".importlinter",
    }


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel:
            continue
        if rel in SKIP_ALL:
            continue
        if any(rel == d or rel.startswith(d + "/") for d in DELETE_PATHS):
            continue
        if is_text(rel, path):
            yield rel, path


# ---------------------------------------------------------------------------
# S1
# ---------------------------------------------------------------------------

def stage_delete(root: Path) -> list[str]:
    removed = []
    for rel in DELETE_PATHS:
        target = root / rel
        if not target.exists():
            continue
        if target.is_dir():
            # shutil handles the Windows quirks (open handles, read-only bits)
            # that make a manual unlink-then-rmdir walk flaky.
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(rel)
    return removed


# ---------------------------------------------------------------------------
# S2
# ---------------------------------------------------------------------------

def stage_identity(root: Path) -> dict[str, int]:
    hits: dict[str, int] = {}
    for rel, path in iter_text_files(root):
        text = read_text(path)
        original = text
        for pattern, replacement, _ in IDENTITY_RULES:
            count = len(re.findall(pattern, text))
            if count:
                hits[pattern] = hits.get(pattern, 0) + count
            text = re.sub(pattern, lambda _m, r=replacement: r, text)
        if text != original:
            write_text(path, text)
    return hits


# ---------------------------------------------------------------------------
# S3  surgical per-file edits
# ---------------------------------------------------------------------------

def _lines(text: str) -> list[str]:
    return text.split("\n")


def _find(lines: list[str], prefix: str, what: str) -> int:
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            return i
    die(f"could not locate {what} (no line starts with {prefix!r})")
    raise AssertionError  # unreachable; keeps type checkers happy


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int]:
    """Return [start, end) for a TOML table, end being the next top-level [..]."""
    try:
        start = lines.index(heading)
    except ValueError:
        die(f"pyproject.toml: table {heading!r} not found")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("["):
            end = i
            break
    return start, end


def _replace_key_line(lines: list[str], prefix: str, new_line: str, what: str) -> None:
    """Replace a single-line key, e.g. ``name = "..."``."""
    i = _find(lines, prefix, what)
    lines[i] = new_line


def _replace_multiline_array(lines: list[str], prefix: str, body: list[str],
                             what: str) -> None:
    """Replace a TOML array that starts on `prefix` and closes on a lone `]`."""
    start = _find(lines, prefix, what)
    end = None
    for i in range(start, len(lines)):
        if lines[i].rstrip() == "]":
            end = i
            break
    if end is None:
        die(f"could not find the closing ']' of {what}")
    lines[start:end + 1] = body


def stage_pyproject(root: Path) -> None:
    path = root / "pyproject.toml"
    lines = _lines(read_text(path))

    project_start, project_end = _section_bounds(lines, "[project]")
    # Work only inside [project] so nothing outside it can be clobbered.
    head, body, tail = (
        lines[:project_start],
        lines[project_start:project_end],
        lines[project_end:],
    )

    _replace_key_line(body, "name = ", f'name = "{DIST_NAME}"', "project name")
    _replace_key_line(body, "description = ", f'description = "{DESCRIPTION}"',
                      "project description")
    _replace_key_line(body, "keywords = ", f"keywords = {KEYWORDS}",
                      "project keywords")
    _replace_multiline_array(
        body, "authors = ", [f"authors = [", f'    {{name = "{HOLDER}"}}', "]"],
        "project authors",
    )
    # `license = "MIT"` and requires-python are intentionally left untouched.

    lines = head + body + tail

    # [project.urls]: replace the whole table, keeping no upstream marketing URLs.
    start, end = _section_bounds(lines, "[project.urls]")
    lines[start:end] = [
        "[project.urls]",
        f'Homepage = "{FORK_URL}#readme"',
        f'Source = "{FORK_URL}"',
        f'Documentation = "{FORK_URL}#readme"',
        f'Changelog = "{FORK_URL}/blob/main/CHANGELOG.md"',
        f'Issues = "{FORK_URL}/issues"',
        f'Upstream = "{UPSTREAM_URL}"',
        "",
    ]

    # [project.scripts]: the console script is the visible CLI name.
    scripts_start, scripts_end = _section_bounds(lines, "[project.scripts]")
    lines[scripts_start:scripts_end] = [
        "[project.scripts]",
        f'{DIST_NAME} = "odoo_mcp.__main__:main"',
        "",
    ]

    write_text(path, "\n".join(lines))


def stage_uv_lock(root: Path) -> None:
    path = root / "uv.lock"
    text = read_text(path)
    # Only the root project entry carries the distributable name; dependency
    # entries and their hashes must stay untouched. Tolerant of both spellings
    # because the identity pass may already have rewritten it.
    new, n = re.subn(r'^name = "(?:odoo-mcp|castadoo-mcp)"$',
                     f'name = "{DIST_NAME}"', text, count=1, flags=re.MULTILINE)
    if n != 1:
        die('uv.lock: root project `name = "..."` entry not found')
    write_text(path, new)


def _set_json_string(root: Path, rel: str, key: str, value: str) -> None:
    """Replace a top-level JSON string field, preserving the rest byte-for-byte."""
    path = root / rel
    if not path.exists():
        die(f"{rel} missing; upstream renamed or removed it")
    text = read_text(path)
    pattern = rf'^(  "{re.escape(key)}": )".*?"(,?)$'
    new, n = re.subn(pattern, lambda m: f'{m.group(1)}"{value}"{m.group(2)}',
                     text, count=1, flags=re.MULTILINE)
    if n != 1:
        die(f"{rel}: could not replace field {key!r}")
    write_text(path, new)


def stage_manifests(root: Path) -> None:
    _set_json_string(
        root, "server.json", "description",
        f"{DISPLAY_NAME}: gated writes, multi-instance, 41 tools. "
        f"Fork of odoo-mcp (MIT).",
    )
    _set_json_string(
        root, "mcpb/manifest.template.json", "description",
        "Talk to any Odoo 16+ database with zero Odoo-side setup. Local stdio "
        "MCP, 41 tools, gated writes. Fork of odoo-mcp (MIT). Requires uv.",
    )
    _set_json_string(
        root, "mcpb/manifest.template.json", "long_description",
        f"{DISPLAY_NAME} turns any Odoo 16+ database into a Model Context "
        f"Protocol server using only your existing credentials - no App Store "
        f"module, no admin access. 41 tools: bounded reads, server-side "
        f"aggregation, schema discovery, diagnostics, migration helpers, local "
        f"BM25 knowledge search, accounting reports, cross-instance fan-out, "
        f"and a gated write workflow with approval tokens and an audit trail. "
        f"Speaks XML-RPC for Odoo 16-18 and External JSON-2 for Odoo 19+. A "
        f"branding fork of odoo-mcp (MIT); see NOTICE.md.",
    )
    _set_json_string(root, "mcpb/manifest.template.json", "display_name",
                     DISPLAY_NAME)

    glama = root / "glama.json"
    if glama.exists():
        text = read_text(glama)
        text = re.sub(r'"maintainers": \[[^\]]*\]',
                      f'"maintainers": ["{FORK_OWNER}"]', text)
        write_text(glama, text)


def stage_claude_md(root: Path) -> None:
    write_text(root / "CLAUDE.md", CLAUDE_MD)


# ---------------------------------------------------------------------------
# S4  promo removal — Markdown only
# ---------------------------------------------------------------------------

def stage_promo(root: Path) -> list[str]:
    """Drop Markdown lines that only advertise the upstream hosted product.

    Restricted to ``.md``: deleting single lines inside JSON/YAML/TOML would
    produce syntactically broken files. Structured files are handled by the
    dedicated stage-3 functions and checked again in S7.
    """
    touched = []
    for rel, path in iter_text_files(root):
        if not rel.endswith(".md"):
            continue
        text = read_text(path)
        if not PROMO.search(text):
            continue
        kept = [line for line in text.split("\n") if not PROMO.search(line)]
        out = "\n".join(kept)
        out = re.sub(r"\n{3,}", "\n\n", out)
        if out != text:
            write_text(path, out)
            touched.append(rel)
    return touched


# ---------------------------------------------------------------------------
# S5  authored whole-file replacements
# ---------------------------------------------------------------------------

def stage_assets(root: Path) -> list[str]:
    assets = Path(__file__).resolve().parent / "assets"
    written = []
    for src in sorted(assets.rglob("*")):
        if not src.is_file() or src.name == "README.head.md":
            continue
        rel = src.relative_to(assets).as_posix()
        write_text(root / rel, src.read_text(encoding="utf-8"))
        written.append(rel)
    return written


def stage_readme(root: Path) -> None:
    path = root / "README.md"
    text = read_text(path)
    marker = "## Highlights"
    idx = text.find(marker)
    if idx == -1:
        die("README.md: anchor '## Highlights' not found; upstream restructured it")

    # Strip promo lines from UPSTREAM's body only. Doing this before the head is
    # prepended keeps the authored block intact: it legitimately cites the
    # upstream URL in its provenance paragraph.
    body_lines = []
    for line in text[idx:].split("\n"):
        if any(line.strip().startswith(p) for p in README_DROP_LINES):
            continue
        if PROMO.search(line):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines)
    body = re.sub(r"\n{3,}", "\n\n", body)

    head = (Path(__file__).resolve().parent / "assets" / "README.head.md").read_text(
        encoding="utf-8"
    )
    write_text(path, head.rstrip("\n") + "\n\n" + body)


def stage_changelog(root: Path) -> None:
    path = root / "CHANGELOG.md"
    text = read_text(path)
    if "Fork notice." in text:
        return  # idempotent
    banner = (
        f"# Changelog\n\n"
        f"> **Fork notice.** This changelog is upstream's, preserved verbatim so\n"
        f"> release history stays traceable. {DISPLAY_NAME} is a branding fork of\n"
        f"> [{UPSTREAM_URL}]({UPSTREAM_URL}) - see [NOTICE.md](./NOTICE.md).\n"
        f"> Entries below describe upstream `odoo-mcp`, which is functionally the\n"
        f"> same server this fork ships as `{DIST_NAME}`.\n\n"
    )
    if text.startswith("# Changelog\n"):
        text = banner + text[len("# Changelog\n"):].lstrip("\n")
    else:
        text = banner + text
    write_text(path, text)


# ---------------------------------------------------------------------------
# S6  CI + packaging metadata
# ---------------------------------------------------------------------------

def stage_ci(root: Path) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    publish = wf / "publish.yml"
    if publish.exists():
        publish.unlink()
    write_text(wf / "ci.yml", CI_WORKFLOW)


def stage_manifest_in(root: Path) -> None:
    path = root / "MANIFEST.in"
    if not path.exists():
        die("MANIFEST.in missing; upstream renamed it")
    text = read_text(path)
    for extra in ("NOTICE.md", "TRADEMARKS.md", "SYNCING.md"):
        if f"include {extra}" not in text:
            text = text.replace("include CHANGELOG.md",
                                f"include CHANGELOG.md\ninclude {extra}", 1)
    write_text(path, text)


# ---------------------------------------------------------------------------
# S7  residual scan
# ---------------------------------------------------------------------------

def stage_residual(root: Path) -> None:
    """Any surviving promo token outside the legal files is a rule gap."""
    offenders = []
    for rel, path in iter_text_files(root):
        if rel in PROMO_ALLOWED:
            continue
        for lineno, line in enumerate(read_text(path).split("\n"), 1):
            if line.strip() in ALLOWED_PROVENANCE_LINES:
                continue
            if PROMO.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:90]}")
    if offenders:
        die(
            "upstream promo tokens survive in non-Markdown or authored-exempt "
            "files:\n  " + "\n  ".join(sorted(offenders))
            + "\nAdd an explicit rule in stage 3 (and a case in the verifier) "
              "instead of relying on line deletion."
        )


# ---------------------------------------------------------------------------
# Static content
# ---------------------------------------------------------------------------

CLAUDE_MD = f"""\
# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this repo is

`{FORK_REPO}` is a **branding fork** of
[`erpipe-org/mcp-odoo`]({UPSTREAM_URL}), published as the `{DIST_NAME}`
distribution. The functional code is upstream's.

## The one rule that matters

**Never hand-edit `rebrand/CASTADOO_MCP`.** That branch is generated:

```bash
git checkout -B rebrand/CASTADOO_MCP upstream/main
git checkout tooling/rebrand -- rebrand/
python rebrand/apply_rebrand.py
git add -A && git commit -m "chore(rebrand): overlay from upstream@<sha> {GENERATED_MARKER}"
```

Any change you want in the fork belongs in `rebrand/apply_rebrand.py` or
`rebrand/assets/` on `tooling/rebrand`, then regenerate. Editing the branch
directly guarantees a conflict on the next upstream sync, which is the exact
problem this structure exists to prevent. `rebrand/verify.py` and the pre-push
hook both fail if the branch is not reproducible.

## Branches

| Branch | Meaning |
| --- | --- |
| `main` | Byte-identical mirror of `upstream/main`. Fast-forward only. Never commit here. |
| `rebrand/CASTADOO_MCP` | Generated overlay. Force-pushed on every sync. |
| `tooling/rebrand` | Source of truth for the rebrand: script, assets, verifier, hook. |

## Compatibility contract

Untouched on purpose, and enforced by a CI job: the Python import package is
`odoo_mcp`, and every environment variable (`ODOO_*`, `ODOO_MCP_*`, `MCP_*`),
tool name, prompt name and `odoo://` resource URI is identical to upstream.
Do not rename them; it would break every existing configuration.

## Quality gates

```bash
python -m ruff check .
python -m mypy src
python -m pytest
python rebrand/verify.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/adding-a-tool.md](docs/adding-a-tool.md) for upstream's workflow.
Never bypass the gated write workflow or the field-ACL choke points.
"""

CI_WORKFLOW = """\
name: CI

# Test-only pipeline for the CASTADOO MCP fork.
# Upstream's publish jobs (PyPI, GHCR, MCP registry, Smithery, .mcpb) are
# intentionally absent: this fork does not publish under upstream's namespaces
# and holds no credentials for its own. See NOTICE.md.

on:
  push:
    branches: [main, "rebrand/**", "tooling/**"]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install package
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev]"
      - name: Lint
        run: python -m ruff check .
      - name: Type check
        run: python -m mypy src
      - name: Run tests
        run: python -m pytest

  compatibility-contract:
    # Guards the deliberate contract: the import package and the environment
    # variable surface must stay identical to upstream.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.10"
      - name: Install package
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e .
      - name: Import module name unchanged
        run: python -c "import odoo_mcp; print('odoo_mcp import OK')"
      - name: Console script health smoke
        run: castadoo-mcp --health
"""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def apply(root: Path) -> None:
    if not (root / "pyproject.toml").exists():
        die(f"{root} does not look like a repo root (no pyproject.toml)")

    # Detect a tree the overlay has already been applied to. On such a tree the
    # upstream anchors are intentionally gone, so the required-anchor assertions
    # must be skipped; every stage below is written to be a no-op in that case,
    # which is what makes a repeated run provably inert.
    already_rebranded = f'name = "{DIST_NAME}"' in read_text(root / "pyproject.toml")

    removed = stage_delete(root)
    hits = stage_identity(root)

    if not already_rebranded:
        for pattern, _, minimum in IDENTITY_RULES:
            if minimum and hits.get(pattern, 0) < minimum:
                die(
                    f"required rule never matched (expected >= {minimum}): "
                    f"{pattern!r}\n  Upstream probably changed shape. Fix the "
                    f"rule in rebrand/apply_rebrand.py - do NOT edit the "
                    f"rebrand branch."
                )
    else:
        print("apply_rebrand: tree already rebranded; anchor checks skipped "
              "(stages run as no-ops to prove idempotency)")

    stage_pyproject(root)
    stage_uv_lock(root)
    stage_manifests(root)
    stage_claude_md(root)
    promo = stage_promo(root)
    stage_assets(root)
    stage_readme(root)
    stage_changelog(root)
    stage_ci(root)
    stage_manifest_in(root)
    stage_residual(root)

    print(f"apply_rebrand: removed {len(removed)} path(s): "
          f"{', '.join(removed) or '(none)'}")
    print(f"apply_rebrand: promo lines removed from {len(promo)} file(s)")
    print("apply_rebrand: OK")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="skip the pristine-tree guard (used by the verifier)")
    args = ap.parse_args()
    root = Path(args.repo).resolve()

    if not args.allow_dirty:
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        offending = [
            line for line in status.splitlines()
            if line.strip()
            and not line[3:].startswith(("rebrand/", "GATES.md", "PLAN.md"))
        ]
        if offending:
            die(
                "working tree has changes outside rebrand/ and planning files. "
                "The overlay must start from a pristine upstream checkout:\n  "
                + "\n  ".join(offending[:10])
            )

    apply(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
