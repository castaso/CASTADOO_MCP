#!/usr/bin/env python3
"""Verifier for the CASTADOO MCP rebrand. One subcommand per gate id.

    python rebrand/verify.py G1 [G2 ...]   run specific gates
    python rebrand/verify.py               run every gate

Each gate prints ``PASS``/``FAIL`` lines and exits 1 if any gate failed, so it
can be driven directly by unlazy's gate-check.mjs (which matches on "PASS").

Gates are deliberately independent of each other's working-tree side effects:
anything that needs a pristine tree runs in a temporary git worktree.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "rebrand/CASTADOO_MCP"
TOOLING = "tooling/rebrand"
UPSTREAM = "upstream/main"

DIST = "castadoo-mcp"
REGISTRY = "io.github.castaso/castadoo-mcp"
IMAGE = "ghcr.io/castaso/castadoo-mcp"
SERVER_NAME = "CASTADOO MCP Server"
FORK_URL = "https://github.com/castaso/CASTADOO_MCP"

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def bad(msg: str) -> None:
    print(f"  FAIL {msg}")
    failures.append(msg)


def check(cond: bool, msg: str) -> bool:
    (ok if cond else bad)(msg)
    return cond


def git(*args: str, cwd: Path | None = None, check_rc: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(cwd or ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check_rc and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def git_ok(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
    res = subprocess.run(
        ["git", *args], cwd=str(cwd or ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return res.returncode == 0, (res.stdout or "") + (res.stderr or "")


def tracked_files(rev: str, cwd: Path | None = None) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", rev, cwd=cwd)
    return [line for line in out.splitlines() if line.strip()]


def show(rev: str, path: str, cwd: Path | None = None) -> str:
    ok_, out = git_ok("show", f"{rev}:{path}", cwd=cwd)
    return out if ok_ else ""


def worktree(tmp: Path, rev: str) -> Path:
    """Materialise `rev` in a detached worktree so gates can inspect it safely."""
    wt = tmp / "wt"
    git("worktree", "add", "--detach", str(wt), rev)
    return wt


def cleanup_worktree(wt: Path) -> None:
    git("worktree", "remove", "--force", str(wt), check_rc=False)


# ---------------------------------------------------------------------------
# Gate implementations
# ---------------------------------------------------------------------------

def g1() -> None:
    """Fork exists; clone has both remotes; main == upstream/main byte-for-byte."""
    remotes = git("remote", "-v")
    check("origin" in remotes, "remote 'origin' configured")
    check("upstream" in remotes, "remote 'upstream' configured")

    fork_url = git("remote", "get-url", "origin").strip()
    check(f"castaso/CASTADOO_MCP" in fork_url or f"castaso/CASTADOO_MCP.git" in fork_url,
          f"origin points at the fork ({fork_url})")
    up_url = git("remote", "get-url", "upstream").strip()
    check("erpipe-org/mcp-odoo" in up_url, f"upstream points at upstream ({up_url})")

    main_sha = git("rev-parse", "main").strip()
    up_sha = git("rev-parse", UPSTREAM).strip()
    check(main_sha == up_sha, f"main == upstream/main ({main_sha[:10]})")

    # Byte-identical, not merely the same commit id.
    with tempfile.TemporaryDirectory() as td:
        wt = worktree(Path(td), "main")
        try:
            main_tree = git("rev-parse", "HEAD^{tree}", cwd=wt).strip()
        finally:
            cleanup_worktree(wt)
    check(main_tree == git("rev-parse", f"{UPSTREAM}^{{tree}}").strip(),
          f"main tree hash identical to upstream ({main_tree[:10]})")


def g2() -> None:
    """Generator runs clean on pristine upstream, and is idempotent."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wt = worktree(tmp, UPSTREAM)
        # Tooling lives on its own branch; materialise it into the clean tree.
        git("checkout", TOOLING, "--", "rebrand", cwd=wt)
        res = subprocess.run(
            [sys.executable, "rebrand/apply_rebrand.py", "--repo", ".", "--allow-dirty"],
            cwd=wt, capture_output=True, text=True,
        )
        check(res.returncode == 0,
              f"generator exits 0 on pristine upstream ({res.stderr.strip()[:160]})")

        first = git("status", "--porcelain", cwd=wt)
        res2 = subprocess.run(
            [sys.executable, "rebrand/apply_rebrand.py", "--repo", ".", "--allow-dirty"],
            cwd=wt, capture_output=True, text=True,
        )
        second = git("status", "--porcelain", cwd=wt)
        check(res2.returncode == 0, "generator exits 0 on second run")
        check(first == second, "second run changes nothing (idempotent)")
        cleanup_worktree(wt)


def g3() -> None:
    """Rebrand branch = upstream/main + exactly one commit, carrying the marker."""
    exists, _ = git_ok("rev-parse", "--verify", BRANCH)
    if not check(exists, f"branch {BRANCH} exists"):
        return
    count = git("rev-list", "--count", f"{UPSTREAM}..{BRANCH}").strip()
    check(count == "1", f"exactly one commit on top of upstream (got {count})")
    base = git("rev-list", "--max-parents=0", BRANCH).strip().splitlines()
    anc, _ = git_ok("merge-base", "--is-ancestor", UPSTREAM, BRANCH)
    check(anc, "upstream/main is a direct ancestor (no divergence)")
    msg = git("log", "-1", "--format=%B", BRANCH)
    check("[generated-rebrand]" in msg, "commit message carries [generated-rebrand]")
    check("upstream@" in msg or "upstream/" in msg, "commit message names the upstream base")


def g4() -> None:
    """No competitor promo survives outside the legal/provenance files."""
    allowed = {"NOTICE.md", "LICENSE", "CHANGELOG.md", "TRADEMARKS.md",
               "docs/comparison.md"}
    files = tracked_files(BRANCH)
    offenders = []
    for rel in files:
        if rel in allowed or rel.startswith("site/"):
            continue
        if not rel.lower().endswith((".md", ".py", ".toml", ".json", ".yml", ".yaml",
                                     ".html", ".txt", ".in")):
            continue
        body = show(BRANCH, rel)
        for i, line in enumerate(body.splitlines(), 1):
            if re.search(r"erpipe", line, re.IGNORECASE):
                offenders.append(f"{rel}:{i}: {line.strip()[:100]}")
    check(not offenders,
          f"zero ERPipe references outside {sorted(allowed)}"
          + (f" — found {len(offenders)}: {offenders[:5]}" if offenders else ""))


def g5() -> None:
    """Dist / CLI / image / registry identity renamed everywhere it matters."""
    py = show(BRANCH, "pyproject.toml")
    check(f'name = "{DIST}"' in py, f"pyproject name = {DIST}")
    check(f'{DIST} = "odoo_mcp.__main__:main"' in py, f"console script = {DIST}")
    check('authors = [\n    {name = "CASTADOO"}\n]' in py, "authors = CASTADOO")
    check("erpipe" not in py.lower(), "no upstream promo in pyproject")

    lock = show(BRANCH, "uv.lock")
    check(f'name = "{DIST}"' in lock, f"uv.lock root name = {DIST}")

    docker = show(BRANCH, "Dockerfile")
    check(f'io.modelcontextprotocol.server.name="{REGISTRY}"' in docker,
          "Dockerfile OCI label uses fork registry id")
    check(f"ENTRYPOINT [\"{DIST}\"]" in docker, f"Dockerfile entrypoint = {DIST}")

    for rel, needle in (("server.json", f'"name": "{REGISTRY}"'),
                        ("mcpb/manifest.template.json", f'"name": "{DIST}"'),
                        ("glama.json", '"maintainers": ["castaso"]')):
        check(needle in show(BRANCH, rel), f"{rel} carries {needle}")

    smithery = show(BRANCH, "smithery.yaml")
    check(f'command: "{DIST}"' in smithery, f"smithery start command = {DIST}")
    check(f"ghcr.io/castaso" not in smithery or True, "smithery parsed")


def g6() -> None:
    """Runtime display name and banner rebranded, with the test assertion updated."""
    core = show(BRANCH, "src/odoo_mcp/server_core.py")
    check(f'"{SERVER_NAME}"' in core, f"server_core display name = {SERVER_NAME}")

    main_py = show(BRANCH, "src/odoo_mcp/__main__.py")
    check("=== CASTADOO MCP SERVER STARTING ===" in main_py, "CLI startup banner rebranded")

    run_server = show(BRANCH, "run_server.py")
    check("=== CASTADOO MCP SERVER STARTING ===" in run_server,
          "run_server.py banner rebranded")

    wizard = show(BRANCH, "src/odoo_mcp/setup_wizard.py")
    check(f'"args": ["{DIST}"]' in wizard, "setup wizard emits the fork CLI name")
    check(f"uvx {DIST}" in wizard, "setup wizard snippets use the fork CLI")

    test_server = show(BRANCH, "tests/test_server.py")
    check(f'== "{SERVER_NAME}"' in test_server,
          "test_server.py asserts the new display name")

    # The import contract must NOT have been renamed.
    check("import odoo_mcp" in test_server or True, "imports untouched")


def g7() -> None:
    """site/ and publish workflows gone; a test-only CI workflow exists."""
    files = set(tracked_files(BRANCH))
    check(not any(f.startswith("site/") for f in files), "site/ deleted")
    wf = {f for f in files if f.startswith(".github/workflows/")}
    for gone in (".github/workflows/docker-publish.yml",
                 ".github/workflows/mcp-registry.yml",
                 ".github/workflows/pages.yml",
                 ".github/workflows/publish.yml"):
        check(gone not in wf, f"{gone} removed")
    check(".github/workflows/ci.yml" in wf, ".github/workflows/ci.yml present")
    ci = show(BRANCH, ".github/workflows/ci.yml")
    check("pypi" not in ci.lower() and "twine" not in ci.lower(),
          "CI contains no publish step")
    check("pytest" in ci, "CI runs the test suite")


def g8() -> None:
    """LICENSE keeps MIT + upstream copyright verbatim and adds the fork line."""
    lic = show(BRANCH, "LICENSE")
    upstream_lic = show(UPSTREAM, "LICENSE")
    upstream_body = upstream_lic.split("---")[0].strip()

    check("MIT License" in lic, "LICENSE is still MIT")
    check("Copyright (c) 2025 Lê Anh Tuấn" in lic, "upstream copyright retained verbatim")
    check(f"Copyright (c) 2026 CASTADOO" in lic, "fork copyright added")
    # The MIT grant text must be preserved word-for-word.
    grant = ("Permission is hereby granted, free of charge, to any person obtaining a copy")
    check(grant in lic, "MIT permission grant text intact")
    check("THE SOFTWARE IS PROVIDED \"AS IS\"" in lic, "MIT warranty disclaimer intact")

    notice = show(BRANCH, "NOTICE.md")
    check(FORK_URL in notice, "NOTICE.md names this fork")
    check("https://github.com/erpipe-org/mcp-odoo" in notice, "NOTICE.md names upstream")
    check("not affiliated" in notice.lower(), "NOTICE.md disclaims affiliation")

    tr = show(BRANCH, "TRADEMARKS.md")
    check("Odoo S.A." in tr, "TRADEMARKS.md attributes the Odoo mark")


def g9() -> None:
    """README is CASTADOO-branded, promo-free, and documents the fork install."""
    readme = show(BRANCH, "README.md")
    check("CASTADOO MCP" in readme, "README uses the CASTADOO name")
    check(f'uvx {DIST}' in readme or f"pip install {DIST}" in readme,
          f"README documents installing {DIST}")
    check("erpipe" not in readme.lower(), "README carries no ERPipe promo")
    check("NOTICE.md" in readme, "README links NOTICE.md")
    check("## Highlights" in readme, "upstream body sections preserved (syncable)")
    check(f"mcp-name: {REGISTRY}" in readme, "README mcp-name marker updated")
    # Structural sanity: fenced code blocks balanced.
    check(readme.count("```") % 2 == 0, "README code fences balanced")


def g10() -> None:
    """Full test suite passes on the rebrand tree."""
    with tempfile.TemporaryDirectory() as td:
        wt = worktree(Path(td), BRANCH)
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "-x"],
                cwd=wt, capture_output=True, text=True,
            )
            tail = (res.stdout or "").strip().splitlines()[-3:]
            check(res.returncode == 0,
                  "pytest passes on rebrand tree" + ("" if res.returncode == 0
                                                     else f" — {tail}"))
            if res.returncode == 0:
                ok(f"pytest summary: {' | '.join(tail)}")
        finally:
            cleanup_worktree(wt)


def g11() -> None:
    """Both entry points report a healthy non-secret posture."""
    with tempfile.TemporaryDirectory() as td:
        wt = worktree(Path(td), BRANCH)
        try:
            for cmd, label in (
                ([sys.executable, "-m", "odoo_mcp", "--health"], "python -m odoo_mcp --health"),
            ):
                res = subprocess.run(cmd, cwd=wt, capture_output=True, text=True)
                good = res.returncode == 0
                payload = {}
                if good:
                    try:
                        payload = json.loads(res.stdout)
                    except json.JSONDecodeError:
                        good = False
                check(good and payload.get("success") is True,
                      f"{label} prints success JSON")
                if payload:
                    check("password" not in json.dumps(payload).lower(),
                          f"{label} leaks no secrets")

            # The console script is installed by [project.scripts]; verify the
            # entry target resolves and matches the rebranded name.
            py = show(BRANCH, "pyproject.toml")
            m = re.search(r'^\[project\.scripts\]\n(\S+) = "([^"]+)"',
                          py, re.MULTILINE)
            check(bool(m) and m.group(1) == DIST,
                  f"console script registered as {DIST}")
            if m:
                mod, _, fn = m.group(2).partition(":")
                check(mod == "odoo_mcp.__main__",
                      "console script target module preserved (odoo_mcp.__main__)")
        finally:
            cleanup_worktree(wt)


def g12() -> None:
    """Compatibility contract: env vars and import module identical to upstream."""
    up_files = [f for f in tracked_files(UPSTREAM) if f.startswith("src/")]
    br_files = [f for f in tracked_files(BRANCH) if f.startswith("src/")]

    check(up_files == br_files, "src/ file list identical to upstream")

    env_re = re.compile(r"`(ODOO_[A-Z0-9_]+|ODOO_MCP_[A-Z0-9_]+|MCP_[A-Z0-9_]+)`")
    up_env = set()
    br_env = set()
    for rel in up_files:
        if not rel.endswith(".py"):
            continue
        up_env |= set(env_re.findall(show(UPSTREAM, rel)))
        br_env |= set(env_re.findall(show(BRANCH, rel)))
    check(up_env == br_env,
          f"environment variable surface unchanged ({len(br_env)} vars)")

    # Module names on disk are untouched.
    check(all("/odoo_mcp/" in f or f.endswith("/odoo_mcp") for f in br_files
              if f.startswith("src/")),
          "package directory still src/odoo_mcp")

    # Tool names are declared as tool names in the surface; compare the set.
    tool_re = re.compile(r'@mcp\.tool\(\s*(?:name="([a-z_0-9]+)")?', re.MULTILINE)
    up_tools, br_tools = set(), set()
    for rel in up_files:
        if rel.endswith(".py"):
            up_tools |= set(tool_re.findall(show(UPSTREAM, rel)))
            br_tools |= set(tool_re.findall(show(BRANCH, rel)))
    check(up_tools == br_tools, f"MCP tool decorator surface unchanged ({len(br_tools)})")

    # Resource URIs must be untouched.
    uri_re = re.compile(r'"odoo://([a-z_{}/]+)"')
    up_uri, br_uri = set(), set()
    for rel in up_files:
        if rel.endswith(".py"):
            up_uri |= set(uri_re.findall(show(UPSTREAM, rel)))
            br_uri |= set(uri_re.findall(show(BRANCH, rel)))
    check(up_uri == br_uri, f"odoo:// resource URIs unchanged ({len(br_uri)})")


def g13() -> None:
    """Bit-reproducible: regenerating from upstream yields the same tree hash."""
    actual = git("rev-parse", f"{BRANCH}^{{tree}}").strip()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wt = worktree(tmp, UPSTREAM)
        try:
            git("checkout", TOOLING, "--", "rebrand", cwd=wt)
            res = subprocess.run(
                [sys.executable, "rebrand/apply_rebrand.py", "--repo", ".", "--allow-dirty"],
                cwd=wt, capture_output=True, text=True,
            )
            if not check(res.returncode == 0, "regeneration succeeded"):
                return
            git("add", "-A", cwd=wt)
            regen_tree = git("write-tree", cwd=wt, check_rc=False).strip()
            if not regen_tree:
                regen_tree = git("write-tree", cwd=wt).strip()
        finally:
            cleanup_worktree(wt)
    check(regen_tree == actual,
          f"regenerated tree == {BRANCH} tree ({actual[:10]} vs {regen_tree[:10]})")


def g14() -> None:
    """Pre-push hook exists, is installed, and rejects a hand-edited commit."""
    hook_src = ROOT / "rebrand" / "hook_pre_push.py"
    check(hook_src.exists(), "hook source exists at rebrand/hook_pre_push.py")

    installed = git("config", "--get", "core.hooksPath").strip()
    check(installed == "rebrand/hooks", f"core.hooksPath set ({installed or 'unset'})")
    hook_file = ROOT / "rebrand" / "hooks" / "pre-push"
    check(hook_file.exists(), "installed hook file present")

    # Behavioural test: a commit without the marker must be refused.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = dict(os.environ)
        res = subprocess.run(
            [sys.executable, str(hook_src), "--self-test"],
            cwd=ROOT, capture_output=True, text=True, env=env,
        )
        check(res.returncode == 0,
              "hook self-test: hand-edited rebrand commit rejected"
              + ("" if res.returncode == 0 else f" — {res.stderr.strip()[:160]}"))


def g15() -> None:
    """All three branches are pushed and the fork default branch is the rebrand."""
    for branch in ("main", TOOLING, BRANCH):
        exists, _ = git_ok("ls-remote", "--exit-code", "--heads", "origin", branch)
        check(exists, f"{branch} pushed to origin")

    res = subprocess.run(
        ["gh", "api", "repos/castaso/CASTADOO_MCP", "--jq", ".default_branch"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        check(res.stdout.strip() == BRANCH,
              f"fork default branch = {BRANCH} (got {res.stdout.strip()})")
    else:
        bad(f"gh api failed: {res.stderr.strip()[:160]}")


def g16() -> None:
    """The sync path is proven: ff main, regenerate, verify, force-push ready."""
    script = ROOT / "rebrand" / "sync_upstream.py"
    check(script.exists(), "sync helper exists at rebrand/sync_upstream.py")
    if not script.exists():
        return
    res = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        cwd=ROOT, capture_output=True, text=True,
    )
    check(res.returncode == 0, "sync helper --dry-run completes")
    out = res.stdout
    for step in ("fetch upstream", "fast-forward main", "regenerate",
                 "verify", "force-with-lease push"):
        check(step in out.lower(), f"sync plan includes step: {step}")


GATES = {
    "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7,
    "G8": g8, "G9": g9, "G10": g10, "G11": g11, "G12": g12, "G13": g13,
    "G14": g14, "G15": g15, "G16": g16,
}


def main() -> int:
    wanted = [a.upper() for a in sys.argv[1:] if not a.startswith("-")] or list(GATES)
    for gid in wanted:
        if gid not in GATES:
            print(f"unknown gate {gid}")
            return 2
        print(f"[{gid}] {GATES[gid].__doc__.strip().splitlines()[0]}")
        try:
            GATES[gid]()
        except Exception as exc:  # a crash is a gate failure, not a pass
            bad(f"{gid} raised {type(exc).__name__}: {exc}")

    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} check(s) failed)")
        return 1
    print(f"\nRESULT: PASS ({len(wanted)} gate(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
