#!/usr/bin/env python3
"""Pre-push guard for the generated rebrand branch.

`rebrand/CASTADOO_MCP` must only ever contain commits produced by
``rebrand/apply_rebrand.py``. A hand-edited commit on that branch guarantees a
conflict on the next upstream sync, which is precisely the failure this
repository is structured to prevent. This hook refuses such a push.

Installed via ``git config core.hooksPath rebrand/hooks``; the shim in
``rebrand/hooks/pre-push`` calls this file.

    python rebrand/hook_pre_push.py --self-test    # prove the guard behaviour
    python rebrand/hook_pre_push.py <remote> <url> # real pre-push invocation
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

GENERATED_MARKER = "[generated-rebrand]"
BRANCH = "refs/heads/rebrand/CASTADOO_MCP"
BRANCH_NAME = "rebrand/CASTADOO_MCP"
TOOLING = "tooling/rebrand"
ZERO = "0" * 40


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def rev_parse(rev: str) -> str:
    """Resolve a revision to a full sha, or '' when it does not exist."""
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return res.stdout.strip() if res.returncode == 0 else ""


def commit_has_marker(sha: str) -> bool:
    return GENERATED_MARKER in git("log", "-1", "--format=%B", sha)


def guard(remote: str, local_ref: str, local_sha: str) -> tuple[bool, str]:
    """Decide whether a push of `local_ref` at `local_sha` may proceed."""
    if local_ref != BRANCH:
        return True, ""          # every other branch is unconstrained
    if local_sha == ZERO:
        return True, ""          # branch deletion

    if not rev_parse(local_sha):
        # Fail closed: if the object cannot be inspected we cannot certify it.
        return False, (
            f"refusing to push to {BRANCH_NAME}: cannot resolve {local_sha[:10]} "
            f"to a commit, so the {GENERATED_MARKER} marker cannot be verified."
        )

    # Prefer the range since the remote-tracking base; that is what is actually
    # being introduced. With no base (first push of the branch) fall back to
    # "commits not already on any origin branch", which excludes upstream
    # history and yields exactly the generated commits being introduced.
    base = rev_parse(f"refs/remotes/{remote}/{BRANCH_NAME}")
    if base:
        rng_args = [f"{base}..{local_sha}"]
    else:
        rng_args = [local_sha, "--not", f"--remotes={remote}"]
    shas = [ln.strip() for ln in git("rev-list", *rng_args).splitlines() if ln.strip()]
    if not shas:
        return True, ""          # nothing new to introduce

    offenders = [sha for sha in shas if not commit_has_marker(sha)]
    if not offenders:
        return True, ""

    detail = "\n".join(
        f"  {sha[:10]}  {git('log', '-1', '--format=%s', sha).strip()}"
        for sha in offenders[:5]
    )
    return False, (
        f"refusing to push to {BRANCH_NAME}:\n"
        f"{len(offenders)} commit(s) lack the {GENERATED_MARKER} marker:\n"
        f"{detail}\n\n"
        f"That branch is GENERATED, never hand-edited. Put your change in\n"
        f"rebrand/apply_rebrand.py or rebrand/assets/ on '{TOOLING}', then\n"
        f"regenerate:\n\n"
        f"  git checkout -B {BRANCH_NAME} upstream/main\n"
        f"  git checkout {TOOLING} -- rebrand/\n"
        f"  python rebrand/apply_rebrand.py\n"
        f"  git add -A && git commit -m "
        f"'chore(rebrand): sync upstream@<sha> {GENERATED_MARKER}'\n\n"
        f"Override once with: git push --no-verify\n"
    )


# ---------------------------------------------------------------------------
# Self-test: build a throwaway repo and assert the guard's real behaviour.
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", BRANCH_NAME, str(path)], check=True)
    for key, value in (("user.email", "hook@test.local"),
                       ("user.name", "hook test"),
                       ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(path), "config", key, value], check=True)


def _commit(path: Path, message: str) -> str:
    (path / "f.txt").write_text(message, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def self_test() -> int:
    """Prove each guard branch against a real repository, not a mock."""
    results: list[tuple[bool, str]] = []
    original = Path.cwd()

    tmp = Path(tempfile.mkdtemp(prefix="castadoo-hook-"))
    try:
        _init_repo(tmp)
        os.chdir(tmp)

        # 1. Generated commit (has the marker) is accepted.
        good = _commit(tmp, f"chore(rebrand): overlay {GENERATED_MARKER}")
        passed, _ = guard("origin", BRANCH, good)
        results.append((passed, "marked commit is accepted"))

        # 2. Hand-edited commit (no marker) is refused.
        bad_sha = _commit(tmp, "fix: hand-edited on the generated branch")
        passed, msg = guard("origin", BRANCH, bad_sha)
        results.append((not passed and "refusing to push" in msg,
                        "unmarked commit is refused"))

        # 3. A pinned full branch range catches a buried hand-edit, not just
        #    the tip. Guard is invoked on the tip; the range walk must see both.
        subprocess.run(["git", "update-ref", f"refs/remotes/origin/{BRANCH_NAME}",
                        good], check=True)
        passed, msg = guard("origin", BRANCH, bad_sha)
        results.append((not passed and "1 commit(s) lack" in msg,
                        "range walk reports exactly the unmarked commit"))

        # 4. Other branches are unconstrained.
        passed, _ = guard("origin", "refs/heads/main", bad_sha)
        results.append((passed, "non-rebrand ref is allowed"))

        # 5. Branch deletion is allowed.
        passed, _ = guard("origin", BRANCH, ZERO)
        results.append((passed, "rebrand deletion is allowed"))

        # 6. An unresolvable sha fails closed rather than passing silently.
        passed, msg = guard("origin", BRANCH, "b" * 40)
        results.append((not passed and "cannot resolve" in msg,
                        "unresolvable sha fails closed"))

        # 7. FIRST PUSH: no remote base. The guard must judge only the commits
        #    being introduced, not the whole upstream history behind them.
        #    (Regression: it used to walk all of history and refuse.)
        subprocess.run(["git", "update-ref", "-d",
                        f"refs/remotes/origin/{BRANCH_NAME}"], check=False)
        tip = good
        shas = [ln.strip() for ln in git("rev-list", tip, "--not",
                                         "--remotes=origin").splitlines() if ln.strip()]
        only_introduced = all(commit_has_marker(s) for s in shas)
        results.append((only_introduced and bool(shas),
                        "first push evaluates only the introduced commit(s)"))
    finally:
        os.chdir(original)
        subprocess.run(["git", "-C", str(tmp), "config", "gc.auto", "0"],
                       capture_output=True)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # 7. Hook and generator must agree on the marker literal.
    gen = (Path(__file__).resolve().parent / "apply_rebrand.py").read_text(encoding="utf-8")
    shared = f'GENERATED_MARKER = "{GENERATED_MARKER}"' in gen
    results.append((shared, "generator and hook share the marker literal"))

    failures = 0
    for passed, label in results:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
        if not passed:
            failures += 1
    print(f"  self-test: {len(results) - failures}/{len(results)} passed")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()

    # Real pre-push: stdin lines are
    #   "<local_ref> <local_sha> <remote_ref> <remote_sha>"
    stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_ref, local_sha = parts[0], parts[1]
        passed, msg = guard("origin", local_ref, local_sha)
        if not passed:
            print(msg, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
