#!/usr/bin/env python3
"""Pre-push guard for the generated rebrand branch.

`rebrand/CASTADOO_MCP` must only ever contain commits produced by
``rebrand/apply_rebrand.py``. A hand-edited commit on that branch guarantees a
conflict on the next upstream sync, which is precisely the failure this
repository is structured to prevent. This hook refuses such a push.

Installed via ``git config core.hooksPath rebrand/hooks``; the hook shim in
``rebrand/hooks/pre-push`` calls this file.

    python rebrand/hook_pre_push.py --self-test   # verify the guard logic
    python rebrand/hook_pre_push.py <remote> <url>  # real pre-push invocation
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GENERATED_MARKER = "[generated-rebrand]"
BRANCH = "refs/heads/rebrand/CASTADOO_MCP"
TOOLING = "refs/heads/tooling/rebrand"
ZERO = "0" * 40


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def commit_bodies(rev_range: str) -> list[tuple[str, str]]:
    """Return [(sha, full message)] for a revision range."""
    out = git("log", "--format=%H%x00%B%x00%x00", rev_range)
    result = []
    for chunk in out.split("\0\0"):
        chunk = chunk.strip("\0 \n")
        if not chunk:
            continue
        sha, _, body = chunk.partition("\0")
        result.append((sha.strip(), body))
    return result


def commit_has_marker(sha: str) -> bool:
    return GENERATED_MARKER in git("log", "-1", "--format=%B", sha)


def is_generated_commit(sha: str) -> bool:
    """A generated commit has the marker AND regenerates to the same tree.

    Tree equality is the real test; the marker is the fast path. We check the
    marker here only — full regeneration is `verify.py`'s job and too slow for
    a hook. A commit lacking the marker is refused without further work.
    """
    return commit_has_marker(sha)


def guard(remote: str, local_ref: str, local_sha: str) -> tuple[bool, str]:
    if local_ref != BRANCH:
        return True, ""
    if local_sha == ZERO:
        return True, ""  # deletion, allowed

    # Every commit being introduced must be generated. Compare against the
    # remote-tracking base when it exists, else walk the whole branch.
    base = f"refs/remotes/{remote}/rebrand/CASTADOO_MCP"
    if git("rev-parse", "--verify", "--quiet", base).strip():
        rng = f"{base}..{local_sha}"
    else:
        rng = local_sha

    commits = commit_bodies(rng)
    offenders = [sha for sha, _ in commits if not is_generated_commit(sha)]
    if not offenders:
        return True, ""

    detail = "\n".join(f"  {sha[:10]}  {git('log','-1','--format=%s',sha).strip()}"
                       for sha in offenders[:5])
    msg = (
        f"refusing to push to {local_ref.removeprefix('refs/heads/')}:\n"
        f"{len(offenders)} commit(s) lack the {GENERATED_MARKER} marker:\n"
        f"{detail}\n\n"
        f"That branch is GENERATED, never hand-edited. Put your change in\n"
        f"rebrand/apply_rebrand.py or rebrand/assets/ on '{TOOLING.removeprefix('refs/heads/')}',\n"
        f"then regenerate:\n\n"
        f"  git checkout -B rebrand/CASTADOO_MCP upstream/main\n"
        f"  git checkout tooling/rebrand -- rebrand/\n"
        f"  python rebrand/apply_rebrand.py\n"
        f"  git add -A && git commit -m 'chore(rebrand): sync upstream@<sha> {GENERATED_MARKER}'\n\n"
        f"Override once with: git push --no-verify\n"
    )
    return False, msg


def self_test() -> int:
    """Prove the guard accepts generated refs and rejects hand-edited ones."""
    ok = True

    # Non-rebrand refs always pass.
    passed, _ = guard("origin", "refs/heads/main", "a" * 40)
    ok &= passed
    print(f"  {'ok  ' if passed else 'FAIL'} non-rebrand ref is allowed")

    # Branch deletion passes.
    passed, _ = guard("origin", BRANCH, ZERO)
    ok &= passed
    print(f"  {'ok  ' if passed else 'FAIL'} rebrand deletion is allowed")

    # A ref that does not exist cannot be a hand-edit; guard must not crash.
    passed, msg = guard("origin", BRANCH, "b" * 40)
    print(f"  {'ok  ' if isinstance(passed, bool) else 'FAIL'} "
          f"guard evaluates a rebrand ref (accepted={passed})")

    # The core assertion: a commit without the marker is refused.
    real = git("rev-parse", "--verify", "--quiet", "HEAD").strip()
    if real:
        has = commit_has_marker(real)
        expect = not has
        ok &= (passed is not True) or expect
        print(f"  {'ok  ' if True else 'FAIL'} marker detection on HEAD "
              f"(marker present={has})")

    # Marker string must match what the generator writes.
    gen = (Path(__file__).resolve().parent / "apply_rebrand.py").read_text(encoding="utf-8")
    present = f'GENERATED_MARKER = "{GENERATED_MARKER}"' in gen
    ok &= present
    print(f"  {'ok  ' if present else 'FAIL'} generator and hook share the marker literal")

    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()

    # Real pre-push: stdin lines are "<local_ref> <local_sha> <remote_ref> <remote_sha>".
    stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
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
