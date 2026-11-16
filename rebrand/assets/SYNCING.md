# Syncing with upstream

This fork tracks [`erpipe-org/mcp-odoo`](https://github.com/erpipe-org/mcp-odoo)
continuously. It is structured so that **merging upstream never produces a
conflict**, because upstream is never merged — the rebrand is regenerated from a
pristine copy.

## Branch model

| Branch | Role | Rule |
| --- | --- | --- |
| `main` | Pristine mirror of `upstream/main` | Fast-forward only. Never commit to it. |
| `rebrand/CASTADOO_MCP` | The published tree: upstream + branding overlay | Generated. Never hand-edit. Force-pushed on sync. |
| `tooling/rebrand` | Generator, assets, verifier, hook, this file | The only place rebrand changes are made. |

```
upstream/main ──fast-forward──► main
      │
      └──reset──► rebrand/CASTADOO_MCP  =  upstream/main + rebrand/apply_rebrand.py
```

## One-command sync

```bash
python rebrand/sync_upstream.py --push
```

That runs, in order:

1. `git fetch upstream --tags --prune`
2. `git checkout main && git merge --ff-only upstream/main`
3. `git checkout -B rebrand/CASTADOO_MCP upstream/main`
4. `git checkout tooling/rebrand -- rebrand` then `python rebrand/apply_rebrand.py`
5. One squashed commit: `chore(rebrand): overlay generated from upstream@<sha> [generated-rebrand]`
6. `python rebrand/verify.py G2 G3 G4 G5 G13`
7. `git push origin main` and `git push --force-with-lease origin rebrand/CASTADOO_MCP`

Prefer to inspect first: `python rebrand/sync_upstream.py --dry-run`.

## When the generator fails

It is designed to fail loudly. If upstream renames a file or rewrites a snippet
the generator depends on, you get an error naming the missing anchor:

```
apply_rebrand: ERROR: required rule never matched (expected >= 1): '"Odoo MCP Server"'
  Upstream probably changed. Fix the rule in rebrand/apply_rebrand.py - do NOT edit the rebrand branch.
```

The fix is always the same, and always on `tooling/rebrand`:

1. `git checkout tooling/rebrand`
2. Update the rule or the asset in `rebrand/apply_rebrand.py` / `rebrand/assets/`
3. `python rebrand/sync_upstream.py --push`

Never resolve it by editing the rebrand branch. That converts a mechanical sync
into a manual merge, which is the failure mode this design removes.

## Making an intentional fork-only change

If you want behaviour that upstream does not have (not just branding), it still
goes through the generator:

- **A file upstream also owns** — add a rule or a whole-file asset to
  `rebrand/apply_rebrand.py`. If upstream later edits that file, your asset wins;
  if upstream removes it, the anchor assertion tells you.
- **A brand-new file upstream does not have** — drop it in `rebrand/assets/`; it
  is copied into the tree verbatim.

This keeps the "regenerate and compare tree hashes" property intact, so
`rebrand/verify.py G13` continues to prove the branch is reproducible.

## Why this stays clean

- No merge is ever executed against upstream, so git never has to reconcile
  diverging edits — the overlay is replayed onto a clean base every time.
- `main` is verified byte-identical (tree hash) to `upstream/main`.
- `rebrand/CASTADOO_MCP` is verified bit-reproducible: regenerating from
  upstream must produce the exact same tree hash, enforced by
  `rebrand/verify.py` and by the pre-push hook.

## Verify at any time

```bash
python rebrand/verify.py          # every gate
python rebrand/verify.py G4 G13   # promo leftovers + reproducibility only
```

## Adoption and the equivalent manual commands

If the hook or helper is unavailable, these are the same steps by hand:

```bash
git fetch upstream --tags
git checkout main && git merge --ff-only upstream/main
git checkout -B rebrand/CASTADOO_MCP upstream/main
git checkout tooling/rebrand -- rebrand
python rebrand/apply_rebrand.py
git add -A
git commit -m "chore(rebrand): overlay generated from upstream@$(git rev-parse --short upstream/main) [generated-rebrand]"
python rebrand/verify.py
git push origin main
git push --force-with-lease origin rebrand/CASTADOO_MCP
```
