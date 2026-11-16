#!/usr/bin/env python3
"""Synchronise this fork with upstream, without merge conflicts.

The rebrand branch is regenerated, not merged, so a sync is:

    fetch upstream
      -> fast-forward main to upstream/main (always a fast-forward)
      -> reset rebrand/CASTADOO_MCP to upstream/main
      -> readd the tooling from tooling/rebrand
      -> run rebrand/apply_rebrand.py
      -> commit one squashed commit carrying [generated-rebrand]
      -> run rebrand/verify.py
      -> push main (fast-forward) and rebrand (force-with-lease)

Because no merge ever happens, there is nothing to conflict on.

    python rebrand/sync_upstream.py --dry-run   # show the plan, change nothing
    python rebrand/sync_upstream.py             # do it
    python rebrand/sync_upstream.py --push      # do it and push
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "rebrand/CASTADOO_MCP"
TOOLING = "tooling/rebrand"
MARKER = "[generated-rebrand]"


def run(cmd: list[str], *, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess:
    printable = " ".join(cmd)
    print(f"  $ {printable}")
    return subprocess.run(
        cmd, cwd=str(cwd), text=True, capture_output=capture,
        encoding="utf-8", errors="replace",
    )


def git(*args: str, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd, capture=capture)


def die(msg: str) -> None:
    print(f"sync_upstream: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without changing anything")
    ap.add_argument("--push", action="store_true",
                    help="push main and the regenerated rebrand branch")
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip verify.py (not recommended)")
    args = ap.parse_args()

    print("sync_upstream: step 1/7 - fetch upstream")
    if args.dry_run:
        print("  (dry run) git fetch upstream --tags --prune")
    else:
        git("fetch", "upstream", "--tags", "--prune", capture=True)

    print("sync_upstream: step 2/7 - fast-forward main to upstream/main")
    up_sha_proc = git("rev-parse", "upstream/main", capture=True) if not args.dry_run else None
    up_sha = (up_sha_proc.stdout.strip() if up_sha_proc else "<upstream-sha>")
    if args.dry_run:
        print("  (dry run) git checkout main && git merge --ff-only upstream/main")
    else:
        git("checkout", "main", capture=True)
        res = git("merge", "--ff-only", "upstream/main", capture=True)
        if res.returncode != 0:
            die("main is not a fast-forward of upstream/main. main must stay a "
                "pristine mirror; reset it with:\n"
                "  git checkout main && git reset --hard upstream/main")

    print(f"sync_upstream: step 3/7 - reset {BRANCH} onto upstream@{up_sha[:10]}")
    if args.dry_run:
        print(f"  (dry run) git checkout -B {BRANCH} upstream/main")
    else:
        git("checkout", "-B", BRANCH, "upstream/main", capture=True)

    print("sync_upstream: step 4/7 - regenerate the overlay")
    if args.dry_run:
        print(f"  (dry run) git checkout {TOOLING} -- rebrand")
        print("  (dry run) python rebrand/apply_rebrand.py")
    else:
        res = git("checkout", TOOLING, "--", "rebrand", capture=True)
        if res.returncode != 0:
            die(f"could not read rebrand/ from {TOOLING}")
        gen = run([sys.executable, "rebrand/apply_rebrand.py", "--allow-dirty"],
                  capture=True)
        if gen.returncode != 0:
            sys.stderr.write(gen.stderr or gen.stdout)
            die("generator failed. Fix the rule in rebrand/apply_rebrand.py on "
                f"{TOOLING} - do NOT hand-edit {BRANCH}.")

    print("sync_upstream: step 5/7 - commit one squashed overlay commit")
    msg = f"chore(rebrand): overlay generated from upstream@{up_sha[:10]} {MARKER}"
    if args.dry_run:
        print(f'  (dry run) git add -A && git commit -m "{msg}"')
    else:
        git("add", "-A", capture=True)
        res = git("commit", "-m", msg, capture=True)
        if res.returncode != 0:
            print("  nothing to commit - already up to date")

    print("sync_upstream: step 6/7 - verify")
    if args.dry_run:
        print("  (dry run) python rebrand/verify.py G2 G3 G4 G5 G13")
    elif args.skip_tests:
        print("  skipped (--skip-tests)")
    else:
        res = run([sys.executable, "rebrand/verify.py", "G2", "G3", "G4", "G5", "G13"],
                  capture=True)
        sys.stdout.write(res.stdout)
        if res.returncode != 0:
            die("verification failed; nothing was pushed")

    print("sync_upstream: step 7/7 - push")
    if args.dry_run or not args.push:
        print("  (not pushed) run again with --push to publish, or push manually:")
        print("    git push origin main")
        print(f"    git push --force-with-lease origin {BRANCH}")
        return 0

    res = git("push", "origin", "main", capture=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr or "")
        die("failed to push main")

    res = git("push", "--force-with-lease", "origin", BRANCH, capture=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr or "")
        die(f"failed to push {BRANCH}")

    print("sync_upstream: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
