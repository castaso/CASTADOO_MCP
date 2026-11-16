# NOTICE

CASTADOO MCP is a **modified fork** of the open-source project
[`erpipe-org/mcp-odoo`](https://github.com/erpipe-org/mcp-odoo) (formerly
`tuanle96/mcp-odoo`), which is distributed under the MIT License.

## Attribution

- **Upstream project:** https://github.com/erpipe-org/mcp-odoo
- **Upstream copyright:** Copyright (c) 2025 Lê Anh Tuấn
- **License:** MIT (see [LICENSE](./LICENSE)); the upstream copyright notice is
  retained verbatim and in full.

## What this fork changed

This fork is a **branding overlay**, not a functional rewrite. The changes are
generated mechanically from upstream by `rebrand/apply_rebrand.py` so that the
fork can be resynchronised with upstream without merge conflicts. The changes are:

1. **Project identity.** Distribution name, console-script name, image name and
   registry identifiers renamed from `odoo-mcp` to `castadoo-mcp`
   (`io.github.castaso/castadoo-mcp`, `ghcr.io/castaso/castadoo-mcp`).
2. **Display branding.** The MCP server display name and startup banner now read
   "CASTADOO MCP". Documentation, examples and agent skills refer to
   `castadoo-mcp`.
3. **Promotional content removed.** Upstream README and manifest content that
   advertises the separately hosted commercial product ("ERPipe") was removed.
   The `site/` marketing pages were deleted.
4. **Publishing disabled.** Upstream CI jobs that publish to PyPI, GHCR, the
   official MCP registry, Smithery and Claude Desktop bundles were removed. Only
   test/lint CI remains.
5. **Legal files added.** This NOTICE, TRADEMARKS.md, and a second copyright line
   in LICENSE for the fork's modifications.

## What this fork did NOT change

Deliberately preserved so that existing configurations, client snippets and
automation keep working unchanged:

- The Python import package **`odoo_mcp`** (`python -m odoo_mcp` still works).
- Every **environment variable** (`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`,
  `ODOO_PASSWORD`, `ODOO_TRANSPORT`, `ODOO_MCP_*`, `MCP_*`).
- Every **MCP tool name**, prompt name and `odoo://` resource URI.
- The safe-write gate, approval-token workflow, field-ACL and audit behaviour.
- Upstream's git history, which is preserved unmodified in `main`.

## No affiliation

This fork is **not affiliated with, endorsed by, or sponsored by** the upstream
maintainers, the ERPipe product, or Odoo S.A. "Odoo" is a trademark of Odoo S.A.
and is used here only to describe interoperability, as permitted by nominative
fair use.

## Support

Upstream does not support this fork. For problems specific to CASTADOO branding,
packaging or the rebrand pipeline, open an issue in this repository. For upstream
bugs, please report them upstream at
https://github.com/erpipe-org/mcp-odoo/issues so everyone benefits.
