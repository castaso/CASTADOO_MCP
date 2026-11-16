# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this repo is

`CASTADOO_MCP` is a **branding fork** of
[`erpipe-org/mcp-odoo`](https://github.com/erpipe-org/mcp-odoo), published as the `castadoo-mcp`
distribution. The functional code is upstream's.

## The one rule that matters

**Never hand-edit `rebrand/CASTADOO_MCP`.** That branch is generated:

```bash
git checkout -B rebrand/CASTADOO_MCP upstream/main
git checkout tooling/rebrand -- rebrand/
python rebrand/apply_rebrand.py
git add -A && git commit -m "chore(rebrand): overlay from upstream@<sha> [generated-rebrand]"
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
