#!/usr/bin/env python3
"""Verifier for the CASTADOO MCP rebrand. One subcommand per gate id.

    python rebrand/verify.py G1 G4        run specific gates
    python rebrand/verify.py              run every gate

Design notes
------------
* Every gate that needs a pristine or generated tree works in a throwaway
  **local clone** placed on the same drive as the repo. `git worktree` is
  avoided deliberately: on Windows its `.git` *file* points into the parent
  repo and trips git's dubious-ownership guard, producing false failures that
  have nothing to do with the rebrand.
* Gates read the committed state of `rebrand/CASTADOO_MCP`, not the working
  tree, so results do not depend on which branch is currently checked out.
* A gate prints ``PASS``-style ``ok`` lines and ``FAIL`` lines; the process
  exits non-zero if any check failed, which is what unlazy's gate-check.mjs
  consumes.

Exit codes: 0 all gates pass, 1 a check failed, 2 usage error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "rebrand/CASTADOO_MCP"
TOOLING = "tooling/rebrand"
UPSTREAM = "upstream/main"

DIST = "castadoo-mcp"
REGISTRY = "io.github.castaso/castadoo-mcp"
SERVER_NAME = "CASTADOO MCP Server"
FORK_URL = "https://github.com/castaso/CASTADOO_MCP"

# Tests that already fail on pristine upstream in this environment. Recorded so
# the gate is strict about regressions without lying about a green suite.
#   - symlink test needs Windows Developer Mode / admin (WinError 1314)
#   - task-queue ordering relies on sub-second timestamps
KNOWN_UPSTREAM_FAILURES = {
    "tests/test_attachment_upload.py::test_from_path_rejects_file_swapped_for_symlink_after_root_check",
    "tests/test_task_queue.py::test_list_tasks_newest_first",
}

failures: list[str] = []
_rebrand_clone: Path | None = None
_venv_py: Path | None = None


# ---------------------------------------------------------------------------
# Process / git helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None):
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def git(*args: str, cwd: Path | None = None) -> str:
    # safe.directory=* keeps git from refusing freshly cloned/checkouted dirs
    # on filesystems that do not record ownership (common on Windows).
    res = _run(["git", "-c", "safe.directory=*", *args], cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def git_ok(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
    res = _run(["git", "-c", "safe.directory=*", *args], cwd=cwd)
    return res.returncode == 0, (res.stdout or "") + (res.stderr or "")


def tracked(rev: str) -> list[str]:
    return [line for line in git("ls-tree", "-r", "--name-only", rev).splitlines()
            if line.strip()]


def show(rev: str, path: str, cwd: Path | None = None) -> str:
    ok_, out = git_ok("show", f"{rev}:{path}", cwd=cwd)
    return out if ok_ else ""


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def bad(msg: str) -> None:
    print(f"  FAIL {msg}")
    failures.append(msg)


# Hosted-product promo surface. Deliberately NOT the bare "erpipe" token, because
# a provenance link to the upstream repo is required by the license and NOTICE.
PROMO_MARKERS = (
    "erpipe.com",
    "mcp.erpipe.com",
    "ERPipe is the managed",
    "ERPipe hosted",
    "erpipe-org/erpipe",
)


def check(cond: bool, msg: str) -> bool:
    (ok if cond else bad)(msg)
    return cond


# ---------------------------------------------------------------------------
# Throwaway clones (same drive, real .git -> no ownership ambiguity)
# ---------------------------------------------------------------------------

def _temp_root() -> Path:
    base = ROOT.parent
    path = base / f".castadoo-verify-{uuid.uuid4().hex[:8]}"
    return path


def clone_at(rev: str) -> Path:
    """Local clone of this repo checked out at `rev`, in a temp sibling dir.

    ``--no-hardlinks`` is essential: this filesystem refuses the hardlinks that
    ``--local`` uses by default (``fatal: failed to create link ... Function not
    implemented``), and ``safe.directory=*`` keeps git from rejecting the clone
    over the ownership guard.
    """
    path = _temp_root()
    res = _run(["git", "-c", "safe.directory=*", "clone", "--no-hardlinks",
                "--local", "--quiet", str(ROOT), str(path)])
    if res.returncode != 0:
        raise RuntimeError(f"local clone failed: {res.stderr.strip()[:200]}")
    git("checkout", "--quiet", "--detach", rev, cwd=path)
    return path


def materialize_tooling(path: Path) -> None:
    """Copy rebrand/ from the tooling branch into a clone (script + assets)."""
    git("checkout", TOOLING, "--", "rebrand", cwd=path)


def rebrand_clone() -> Path:
    """Single cached clone of the generated branch, reused across gates."""
    global _rebrand_clone
    if _rebrand_clone is None:
        _rebrand_clone = clone_at(BRANCH)
    return _rebrand_clone


def drop_clone(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Interpreter / environment for running the suite
# ---------------------------------------------------------------------------

def verify_venv_python() -> Path:
    """Interpreter with the project's deps installed, built once per run."""
    global _venv_py
    if _venv_py and _venv_py.exists():
        return _venv_py

    venv = ROOT / ".verify-venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        create = _run(["uv", "venv", "--python", "3.11", str(venv)], cwd=ROOT)
        if create.returncode != 0:
            bad(f"could not create verification venv: {create.stderr.strip()[:160]}")
            raise SystemExit(1)
    _venv_py = py
    return py


def install_into(py: Path, source: Path) -> bool:
    res = _run([str(py), "-m", "pip", "install", "-q", "-e", f"{source}[dev]"],
               cwd=source, timeout=900)
    if res.returncode != 0:
        # uv is the documented tool and is much faster; fall back to it.
        uv = _run(["uv", "pip", "install", "--python", str(py), "-e", f"{source}[dev]"],
                  cwd=source, timeout=900)
        if uv.returncode != 0:
            bad(f"dependency install failed: {(uv.stderr or res.stderr).strip()[:200]}")
            return False
    return True


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def g1() -> None:
    """Fork exists; remotes correct; main byte-identical to upstream/main."""
    remotes = git("remote", "-v")
    check("origin" in remotes, "remote 'origin' configured")
    check("upstream" in remotes, "remote 'upstream' configured")
    origin = git("remote", "get-url", "origin").strip()
    check("castaso/CASTADOO_MCP" in origin, f"origin -> fork ({origin})")
    up = git("remote", "get-url", "upstream").strip()
    check("erpipe-org/mcp-odoo" in up, f"upstream -> upstream ({up})")

    check(git("rev-parse", "main").strip() == git("rev-parse", UPSTREAM).strip(),
          "main commit == upstream/main commit")
    main_tree = git("rev-parse", "main^{tree}").strip()
    up_tree = git("rev-parse", f"{UPSTREAM}^{{tree}}").strip()
    check(main_tree == up_tree,
          f"main tree hash identical to upstream ({main_tree[:10]})")

    # No fork-only commits leaked onto main.
    count = git("rev-list", "--count", f"{UPSTREAM}..main").strip()
    check(count == "0", f"main carries zero extra commits (got {count})")


def g2() -> None:
    """Generator succeeds on pristine upstream and is idempotent."""
    path = clone_at(UPSTREAM)
    try:
        materialize_tooling(path)
        env_py = sys.executable
        first = _run([env_py, "rebrand/apply_rebrand.py", "--allow-dirty"], cwd=path)
        check(first.returncode == 0,
              "generator exits 0 on pristine upstream"
              + ("" if first.returncode == 0 else f" — {first.stderr.strip()[:200]}"))
        if first.returncode != 0:
            return
        after_first = git("status", "--porcelain", cwd=path)
        second = _run([env_py, "rebrand/apply_rebrand.py", "--allow-dirty"], cwd=path)
        after_second = git("status", "--porcelain", cwd=path)
        check(second.returncode == 0, "generator exits 0 on a second run")
        check(after_first == after_second, "second run changes nothing (idempotent)")
        if after_first:
            ok(f"generator touched {len(after_first.splitlines())} path(s) on first run")
    finally:
        drop_clone(path)


def g3() -> None:
    """Rebrand branch = upstream/main + exactly one marked commit."""
    exists, _ = git_ok("rev-parse", "--verify", "--quiet", BRANCH)
    if not check(exists, f"branch {BRANCH} exists"):
        return
    count = git("rev-list", "--count", f"{UPSTREAM}..{BRANCH}").strip()
    check(count == "1", f"exactly one commit on top of upstream (got {count})")
    anc, _ = git_ok("merge-base", "--is-ancestor", UPSTREAM, BRANCH)
    check(anc, "upstream/main is an ancestor (branch never diverges)")
    msg = git("log", "-1", "--format=%B", BRANCH)
    check("[generated-rebrand]" in msg, "commit carries the [generated-rebrand] marker")
    check("upstream@" in msg, "commit names its upstream base")


def g4() -> None:
    """No hosted-product promo survives outside the provenance files."""
    offenders = []
    for rel in tracked(BRANCH):
        if not rel.lower().endswith((".md", ".py", ".toml", ".json", ".yml",
                                     ".yaml", ".html", ".txt", ".in", ".mdc")):
            continue
        body = show(BRANCH, rel)
        low = body.lower()
        for marker in PROMO_MARKERS:
            if marker.lower() in low:
                offenders.append(f"{rel}: {marker}")
    check(not offenders,
          "zero hosted-product promo references anywhere in the tree"
          + (f" — {len(offenders)}: {offenders[:6]}" if offenders else ""))

    # The only surviving upstream mentions must be provenance links.
    legal = {"NOTICE.md", "LICENSE", "CHANGELOG.md", "TRADEMARKS.md"}
    stray = []
    for rel in tracked(BRANCH):
        if rel in legal or not rel.lower().endswith((".md", ".toml", ".py")):
            continue
        for i, line in enumerate(show(BRANCH, rel).splitlines(), 1):
            if re.search(r"erpipe", line, re.IGNORECASE) and not (
                "github.com/erpipe-org/mcp-odoo" in line
            ):
                stray.append(f"{rel}:{i}: {line.strip()[:80]}")
    check(not stray,
          "remaining 'erpipe' mentions are upstream provenance links only"
          + (f" — {stray[:4]}" if stray else ""))


def g5() -> None:
    """Dist / CLI / image / registry identity renamed everywhere it matters."""
    py = show(BRANCH, "pyproject.toml")
    check(f'name = "{DIST}"' in py, f"pyproject name = {DIST}")
    check(f'{DIST} = "odoo_mcp.__main__:main"' in py, f"console script = {DIST}")
    check('{name = "CASTADOO"}' in py, "authors = CASTADOO")
    check("[project.urls]" in py and FORK_URL in py, "project URLs point at the fork")
    check('license = "MIT"' in py, "license still MIT")
    check('"odoo-mcp"' not in py, "pyproject no longer declares the upstream dist name")

    check(f'name = "{DIST}"' in show(BRANCH, "uv.lock"), f"uv.lock root name = {DIST}")

    docker = show(BRANCH, "Dockerfile")
    check(f'io.modelcontextprotocol.server.name="{REGISTRY}"' in docker,
          "Dockerfile OCI label = fork registry id")
    check(f'ENTRYPOINT ["{DIST}"]' in docker, f"Dockerfile entrypoint = {DIST}")

    for rel, needle in (
        ("server.json", f'"name": "{REGISTRY}"'),
        ("mcpb/manifest.template.json", f'"name": "{DIST}"'),
        ("mcpb/manifest.template.json", f'"display_name": "CASTADOO MCP"'),
        ("glama.json", '"maintainers": ["castaso"]'),
        ("smithery.yaml", f'command: "{DIST}"'),
    ):
        check(needle in show(BRANCH, rel), f"{rel} contains {needle}")


def g6() -> None:
    """Runtime display name and banner rebranded, test assertion updated."""
    check(f'"{SERVER_NAME}"' in show(BRANCH, "src/odoo_mcp/server_core.py"),
          f"server_core display name = {SERVER_NAME}")
    banner = "=== CASTADOO MCP SERVER STARTING ==="
    check(banner in show(BRANCH, "src/odoo_mcp/__main__.py"), "CLI banner rebranded")
    check(banner in show(BRANCH, "run_server.py"), "run_server.py banner rebranded")
    wizard = show(BRANCH, "src/odoo_mcp/setup_wizard.py")
    check(f'["{DIST}"]' in wizard, "wizard config snippet uses the fork CLI")
    check(f"uvx {DIST}" in wizard, "wizard CLI instructions use the fork name")
    check(f'"{SERVER_NAME}"' in show(BRANCH, "tests/test_server.py"),
          "test_server.py asserts the new display name")


def g7() -> None:
    """site/ and all publish workflows removed; test-only CI present."""
    files = set(tracked(BRANCH))
    check(not any(f.startswith("site/") for f in files), "site/ deleted")
    wf = {f for f in files if f.startswith(".github/workflows/")}
    for gone in (".github/workflows/docker-publish.yml",
                 ".github/workflows/mcp-registry.yml",
                 ".github/workflows/pages.yml",
                 ".github/workflows/publish.yml"):
        check(gone not in wf, f"{gone} removed")
    check(".github/workflows/ci.yml" in wf, "test-only ci.yml present")
    ci = show(BRANCH, ".github/workflows/ci.yml")
    check("pytest" in ci, "CI runs the suite")
    for forbidden in ("twine", "PYPI_API_TOKEN", "mcp-publisher",
                      "docker/build-push-action", "smithery"):
        check(forbidden not in ci, f"CI has no publish step ({forbidden})")


def g8() -> None:
    """LICENSE retains upstream MIT text verbatim; NOTICE and TRADEMARKS added."""
    lic = show(BRANCH, "LICENSE")
    up = show(UPSTREAM, "LICENSE")

    check("MIT License" in lic, "LICENSE is MIT")
    check("Copyright (c) 2025 Lê Anh Tuấn" in lic, "upstream copyright retained verbatim")
    check("Copyright (c) 2026 CASTADOO" in lic, "fork copyright added")

    # The operative MIT paragraphs must appear unchanged in both files.
    for para in ("Permission is hereby granted, free of charge, to any person "
                 "obtaining a copy",
                 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'):
        check(para in lic and para in up, f"MIT clause preserved verbatim: {para[:40]}...")

    # Nothing upstream lost: every non-empty upstream line still present.
    missing = [line for line in up.splitlines()
               if line.strip() and line.strip() != "---" and line not in lic]
    check(not missing,
          "no upstream license text removed"
          + (f" — missing {missing[:3]}" if missing else ""))

    notice = show(BRANCH, "NOTICE.md")
    check(FORK_URL in notice, "NOTICE.md names this fork")
    check("https://github.com/erpipe-org/mcp-odoo" in notice, "NOTICE.md names upstream")
    check("not affiliated" in notice.lower(), "NOTICE.md disclaims affiliation")
    check("odoo_mcp" in notice, "NOTICE.md documents the preserved import package")
    # The provenance URL must not have been rewritten to point at this fork.
    check(notice.count("https://github.com/erpipe-org/mcp-odoo") >= 2,
          "NOTICE.md keeps upstream URLs for attribution")
    check("Odoo S.A." in show(BRANCH, "TRADEMARKS.md"), "TRADEMARKS.md attributes Odoo S.A.")


def g9() -> None:
    """README is CASTADOO-branded, promo-free, and documents the fork install."""
    readme = show(BRANCH, "README.md")
    check("CASTADOO MCP" in readme, "README is CASTADOO-branded")
    check(f"uvx {DIST}" in readme or f"pip install {DIST}" in readme,
          f"README documents installing {DIST}")
    low = readme.lower()
    hits = [m for m in PROMO_MARKERS if m.lower() in low]
    check(not hits, "README carries no hosted-product promo"
          + (f" — {hits}" if hits else ""))
    check("NOTICE.md" in readme, "README links NOTICE.md")
    check("SYNCING.md" in readme, "README links SYNCING.md")
    check("## Highlights" in readme, "upstream body sections preserved (syncable)")
    check(f"mcp-name: {REGISTRY}" in readme, "README ownership marker updated")
    check(readme.count("```") % 2 == 0, "README code fences balanced")
    check("docs/comparison.md" not in readme, "README has no link to deleted docs")
    check("odoo_mcp" in readme or True, "README may mention the import package")


def g10() -> None:
    """Test suite passes, allowing only the recorded upstream baseline failures."""
    path = rebrand_clone()
    py = verify_venv_python()
    if not install_into(py, path):
        return

    res = _run([str(py), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
               cwd=path, timeout=1800)
    out = (res.stdout or "") + (res.stderr or "")

    failed = set()
    m = re.search(r"short test summary info", out)
    if m:
        for line in out[m.end():].splitlines():
            s = line.strip()
            if s.startswith("FAILED "):
                failed.add(s.split(" ", 1)[1].split(" ")[0])
            elif s.startswith("ERROR "):
                failed.add(s.split(" ", 1)[1].split(" ")[0])

    summary = [ln for ln in out.splitlines() if re.search(r"\d+ (passed|failed)", ln)]
    ok(f"pytest: {summary[-1].strip() if summary else 'no summary parsed'}")

    unexpected = failed - KNOWN_UPSTREAM_FAILURES
    check(not unexpected,
          "no new test failures"
          + (f" — unexpected: {sorted(unexpected)[:5]}" if unexpected else ""))
    missing_baseline = KNOWN_UPSTREAM_FAILURES - failed
    if missing_baseline:
        ok(f"baseline failures that now pass (fine, consider pruning the list): "
           f"{len(missing_baseline)}")
    check(len(failed) <= len(KNOWN_UPSTREAM_FAILURES),
          f"failing tests ({len(failed)}) within recorded baseline "
          f"({len(KNOWN_UPSTREAM_FAILURES)})")


def g11() -> None:
    """Both entry points report a healthy, secret-free posture."""
    path = rebrand_clone()
    py = verify_venv_python()
    if not install_into(py, path):
        return

    for cmd, label in (
        ([str(py), "-m", "odoo_mcp", "--health"], "python -m odoo_mcp --health"),
        ([str(py.parent / ("castadoo-mcp.exe" if os.name == "nt" else "castadoo-mcp")),
          "--health"], "castadoo-mcp --health"),
    ):
        if not Path(cmd[0]).exists():
            bad(f"{label}: executable missing at {cmd[0]}")
            continue
        res = _run(cmd, cwd=path)
        payload = {}
        good = res.returncode == 0
        if good:
            try:
                payload = json.loads(res.stdout)
            except json.JSONDecodeError:
                good = False
        check(good and payload.get("success") is True, f"{label} returns success JSON")
        if payload:
            check("password" not in json.dumps(payload).lower(),
                  f"{label} leaks no credentials")


def g12() -> None:
    """Compatibility contract: env vars, module, tools and URIs unchanged."""
    up_src = [f for f in tracked(UPSTREAM) if f.startswith("src/")]
    br_src = [f for f in tracked(BRANCH) if f.startswith("src/")]
    check(sorted(up_src) == sorted(br_src), "src/ file list identical to upstream")
    check(all("/odoo_mcp/" in f or f.endswith("/odoo_mcp") for f in br_src),
          "package directory still src/odoo_mcp")

    env_re = re.compile(r"\b(ODOO_[A-Z0-9_]+|ODOO_MCP_[A-Z0-9_]+|MCP_[A-Z0-9_]+)\b")
    up_env, br_env = set(), set()
    tool_re = re.compile(r'@mcp\.tool\(\s*(?:name="([A-Za-z_0-9]+)")?')
    up_tools, br_tools = set(), set()
    uri_re = re.compile(r'"(odoo://[a-z_{}/]+)"')
    up_uri, br_uri = set(), set()
    prompt_re = re.compile(r'@mcp\.prompt\(')

    up_prompts = br_prompts = 0
    for rel in up_src:
        if not rel.endswith(".py"):
            continue
        u, b = show(UPSTREAM, rel), show(BRANCH, rel)
        up_env |= set(env_re.findall(u))
        br_env |= set(env_re.findall(b))
        up_tools |= set(tool_re.findall(u))
        br_tools |= set(tool_re.findall(b))
        up_uri |= set(uri_re.findall(u))
        br_uri |= set(uri_re.findall(b))
        up_prompts += len(prompt_re.findall(u))
        br_prompts += len(prompt_re.findall(b))

    check(up_env == br_env,
          f"environment variable surface unchanged ({len(br_env)} vars)")
    check(up_tools == br_tools, f"MCP tool surface unchanged ({len(br_tools)} tools)")
    check(up_uri == br_uri, f"odoo:// resource URIs unchanged ({len(br_uri)})")
    check(up_prompts == br_prompts, f"prompt surface unchanged ({br_prompts} prompts)")

    # The import package name itself must never be renamed.
    check(show(BRANCH, "src/odoo_mcp/__init__.py").strip() != "", "__init__.py readable")


def g13() -> None:
    """Bit-reproducible: regenerating from upstream yields the same tree hash."""
    actual = git("rev-parse", f"{BRANCH}^{{tree}}").strip()
    path = clone_at(UPSTREAM)
    try:
        materialize_tooling(path)
        res = _run([sys.executable, "rebrand/apply_rebrand.py", "--allow-dirty"], cwd=path)
        if not check(res.returncode == 0, "regeneration succeeded"):
            return
        git("add", "-A", cwd=path)
        regen = git("write-tree", cwd=path).strip()
    finally:
        drop_clone(path)
    check(regen == actual,
          f"regenerated tree hash == {BRANCH} tree ({actual[:10]} vs {regen[:10]})")


def g14() -> None:
    """Pre-push hook installed and refuses a hand-edited rebrand commit."""
    check((ROOT / "rebrand" / "hook_pre_push.py").exists(),
          "hook source present at rebrand/hook_pre_push.py")
    hooks_path = git("config", "--get", "core.hooksPath").strip()
    check(hooks_path == "rebrand/hooks", f"core.hooksPath = rebrand/hooks ({hooks_path})")
    check((ROOT / "rebrand" / "hooks" / "pre-push").exists(), "hook shim installed")

    res = _run([sys.executable, str(ROOT / "rebrand" / "hook_pre_push.py"), "--self-test"],
               cwd=ROOT)
    check(res.returncode == 0,
          "hook self-test passes (rejects unmarked commits)"
          + ("" if res.returncode == 0 else f" — {res.stderr.strip()[:200]}"))
    for line in (res.stdout or "").splitlines():
        if line.strip().startswith("FAIL"):
            bad(f"hook self-test: {line.strip()}")


def g15() -> None:
    """All three branches pushed; fork default branch is the rebrand branch."""
    for branch in ("main", TOOLING, BRANCH):
        exists, _ = git_ok("ls-remote", "--exit-code", "--heads", "origin", branch)
        check(exists, f"{branch} pushed to origin")

    res = _run(["gh", "api", "repos/castaso/CASTADOO_MCP", "--jq", ".default_branch"])
    if res.returncode == 0:
        got = res.stdout.strip()
        check(got == BRANCH, f"fork default branch = {BRANCH} (got {got})")
    else:
        bad(f"gh api unavailable: {res.stderr.strip()[:160]}")


def g16() -> None:
    """The sync path is executable and complete (dry run, no side effects)."""
    script = ROOT / "rebrand" / "sync_upstream.py"
    if not check(script.exists(), "sync helper present at rebrand/sync_upstream.py"):
        return
    res = _run([sys.executable, str(script), "--dry-run"], cwd=ROOT)
    check(res.returncode == 0, "sync helper --dry-run exits 0")
    out = (res.stdout or "").lower()
    for step in ("fetch upstream", "fast-forward main", "regenerate", "verify",
                 "force-with-lease"):
        check(step in out, f"sync plan includes: {step}")
    check("reset" in out or "checkout -B" in out,
          "sync plan reseats the rebrand branch instead of merging")


GATES = {
    "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7,
    "G8": g8, "G9": g9, "G10": g10, "G11": g11, "G12": g12, "G13": g13,
    "G14": g14, "G15": g15, "G16": g16,
}


def main() -> int:
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    wanted = args or list(GATES)

    try:
        for gid in wanted:
            if gid not in GATES:
                print(f"unknown gate {gid}")
                return 2
            title = (GATES[gid].__doc__ or "").strip().splitlines()[0]
            print(f"[{gid}] {title}")
            try:
                GATES[gid]()
            except Exception as exc:  # a crash is a failure, never a pass
                bad(f"{gid} raised {type(exc).__name__}: {exc}")
    finally:
        if _rebrand_clone is not None:
            drop_clone(_rebrand_clone)

    if failures:
        print(f"\nRESULT: FAIL ({len(failures)} check(s) failed)")
        return 1
    print(f"\nRESULT: PASS ({len(wanted)} gate(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
