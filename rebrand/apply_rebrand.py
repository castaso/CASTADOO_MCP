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

Stage order (important, each stage depends on the previous one's invariants):

    S1 delete upstream-only paths
    S2 regex identity pass over upstream text files (authored files excluded)
    S3 surgical, per-file edits that regex cannot express safely
    S4 promo removal (line-level) on remaining upstream prose
    S5 authored whole-file replacements (README head, LICENSE, NOTICE, ...)
    S6 generated CI workflow + MANIFEST.in augmentation

Authored files are written in S5 and are excluded from S2/S4, otherwise the
promo stripper would delete NOTICE.md's own attribution links.

Usage
-----
    python rebrand/apply_rebrand.py [--repo PATH]
"""

from __future__ import annotations

import argparse
import re
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
    "docs/comparison.md",
}

# Rewritten by dedicated functions; excluded from the blanket regex pass.
DEDICATED = {"README.md", "CHANGELOG.md", "pyproject.toml", "uv.lock"}

# Tooling must never rewrite itself.
TOOLING = {"rebrand/apply_rebrand.py", "rebrand/verify.py",
           "rebrand/assets/README.head.md"}

SKIP_ALL = AUTHORED | DEDICATED | TOOLING | {"GATES.md", "PLAN.md"}

DELETE_PATHS = [
    "site",
    ".github/workflows/docker-publish.yml",
    ".github/workflows/mcp-registry.yml",
    ".github/workflows/pages.yml",
    ".github/FUNDING.yml",
]

# ---------------------------------------------------------------------------
# S2 identity rules. `minimum` > 0 means "must match somewhere or abort".
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
    # Bare dist/CLI token last, so composite names above win first.
    (r"\bodoo-mcp\b", DIST_NAME, 1),
]

PROMO = re.compile(r"erpipe|odoo erp gateway", re.IGNORECASE)


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
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            target.rmdir()
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

def stage_pyproject(root: Path) -> None:
    path = root / "pyproject.toml"
    text = read_text(path)

    def sub(pattern: str, replacement: str, what: str, flags=re.MULTILINE) -> None:
        nonlocal text
        new, n = re.subn(pattern, lambda _m: replacement, text, flags=flags)
        if n == 0:
            die(f"pyproject.toml: could not locate {what} ({pattern!r})")
        text = new

    sub(r'^name = "[^"]+"$', f'name = "{DIST_NAME}"', 'project name')
    sub(r'^description = .+$',
        f'description = "{DESCRIPTION}"', 'project description')
    sub(r'^keywords = \[.*?\]$',
        'keywords = ["odoo", "mcp", "server", "model-context-protocol", '
        '"chatgpt", "claude"]',
        'keywords', flags=re.MULTILINE | re.DOTALL)
    sub(r'^authors = \[.*?^\]$',
        f'authors = [\n    {{name = "{HOLDER}"}}\n]',
        'authors', flags=re.MULTILINE | re.DOTALL)
    sub(r'^\[project\.urls\]\n(?:[^\[].*\n|\n)*',
        "\n".join([
            "[project.urls]",
            f'Homepage = "{FORK_URL}#readme"',
            f'Source = "{FORK_URL}"',
            f'Documentation = "{FORK_URL}#readme"',
            f'Changelog = "{FORK_URL}/blob/main/CHANGELOG.md"',
            f'Issues = "{FORK_URL}/issues"',
            f'Upstream = "{UPSTREAM_URL}"',
            "",
        ]),
        'project urls')
    sub(r'^odoo-mcp = "odoo_mcp\.__main__:main"$',
        f'{DIST_NAME} = "odoo_mcp.__main__:main"', 'console script')

    write_text(path, text)


def stage_uv_lock(root: Path) -> None:
    path = root / "uv.lock"
    text = read_text(path)
    # Only the root project entry carries the distributable name; dependency
    # entries and their hashes must stay untouched.
    new, n = re.subn(r'^name = "odoo-mcp"$', f'name = "{DIST_NAME}"',
                     text, count=1, flags=re.MULTILINE)
    if n != 1:
        die('uv.lock: root `name = "odoo-mcp"` entry not found')
    write_text(path, new)


def stage_manifests(root: Path) -> None:
    """Manifest files where promotion lives in dedicated keys."""
    server = root / "server.json"
    if server.exists():
        text = read_text(server)
        text = re.sub(r'^  "description": .+$',
                      f'  "description": "{DISPLAY_NAME}: gated writes, '
                      f'multi-instance, 41 tools. Fork of odoo-mcp (MIT)."',
                      text, count=1, flags=re.MULTILINE)
        write_text(server, text)

    mcpb = root / "mcpb" / "manifest.template.json"
    if mcpb.exists():
        text = read_text(mcpb)
        text = re.sub(
            r'^  "description": .+$',
            f'  "description": "Talk to any Odoo 16+ database with zero '
            f'Odoo-side setup. Local stdio MCP, 41 tools, gated writes. '
            f'Fork of odoo-mcp (MIT). Requires uv."',
            text, count=1, flags=re.MULTILINE)
        text = re.sub(
            r'^  "long_description": .+$',
            f'  "long_description": "{DISPLAY_NAME} turns any Odoo 16+ database '
            f'into a Model Context Protocol server using only your existing '
            f'credentials - no App Store module, no admin access. 41 tools: '
            f'bounded reads, server-side aggregation, schema discovery, '
            f'diagnostics, migration helpers, local BM25 knowledge search, '
            f'accounting reports, cross-instance fan-out, and a gated write '
            f'workflow with approval tokens and an audit trail. Speaks XML-RPC '
            f'for Odoo 16-18 and External JSON-2 for Odoo 19+. A branding fork '
            f'of odoo-mcp (MIT); see NOTICE.md."',
            text, count=1, flags=re.MULTILINE)
        text = re.sub(
            r'^  "author": \{\n(?:.*\n)*?  \},?$',
            f'  "author": {{\n    "name": "{HOLDER}",\n    "url": "{FORK_URL}"\n  }},',
            text, count=1, flags=re.MULTILINE)
        write_text(mcpb, text)

    glama = root / "glama.json"
    if glama.exists():
        text = read_text(glama)
        text = re.sub(r'"maintainers": \[[^\]]*\]',
                      f'"maintainers": ["{FORK_OWNER}"]', text)
        write_text(glama, text)


def stage_claude_md(root: Path) -> None:
    (root / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# S4  promo removal
# ---------------------------------------------------------------------------

TABLE_SEP = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


def stage_promo(root: Path) -> list[str]:
    """Drop lines that only exist to advertise the upstream hosted product."""
    touched = []
    for rel, path in iter_text_files(root):
        text = read_text(path)
        if not PROMO.search(text):
            continue
        lines = text.split("\n")
        kept: list[str] = []
        for line in lines:
            if PROMO.search(line):
                continue
            kept.append(line)
        # Remove table separator rows orphaned by the deletions above.
        cleaned: list[str] = []
        for i, line in enumerate(kept):
            if TABLE_SEP.match(line):
                prev = cleaned[-1] if cleaned else ""
                nxt_idx = i + 1
                nxt = kept[nxt_idx] if nxt_idx < len(kept) else ""
                if not prev.lstrip().startswith("|") and not nxt.lstrip().startswith("|"):
                    continue
            cleaned.append(line)
        out = "\n".join(cleaned)
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
    head = (Path(__file__).resolve().parent / "assets" / "README.head.md").read_text(
        encoding="utf-8"
    )
    marker = "## Highlights"
    idx = text.find(marker)
    if idx == -1:
        die("README.md: anchor '## Highlights' not found; upstream restructured it")
    text = head.rstrip("\n") + "\n\n" + text[idx:]
    text = re.sub(r"\n{3,}", "\n\n", text)
    write_text(path, text)


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
git add -A && git commit -m "chore(rebrand): ... {GENERATED_MARKER}"
```

Any change you want in the fork belongs in `rebrand/apply_rebrand.py` or
`rebrand/assets/` on `tooling/rebrand`, then regenerate. Editing the branch
directly guarantees a conflict on the next upstream sync, which is the exact
problem this structure exists to prevent. `rebrand/verify.py` and the
pre-push hook both fail if the branch is not reproducible.

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

    removed = stage_delete(root)
    hits = stage_identity(root)

    for pattern, _, minimum in IDENTITY_RULES:
        if minimum and hits.get(pattern, 0) < minimum:
            die(
                f"required rule never matched (expected >= {minimum}): {pattern!r}\n"
                f"  Upstream probably changed shape. Fix the rule in "
                f"rebrand/apply_rebrand.py - do NOT edit the rebrand branch."
            )

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

    print(f"apply_rebrand: removed {len(removed)} path(s): "
          f"{', '.join(removed) or '(none)'}")
    print(f"apply_rebrand: promo lines removed from {len(promo)} file(s)")
    print("apply_rebrand: OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
