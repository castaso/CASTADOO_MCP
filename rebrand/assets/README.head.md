<p align="center">
  <strong>CASTADOO MCP — the free AI layer for Odoo. Any edition, any version.</strong><br>
  Odoo's built-in AI is Enterprise-only. CASTADOO MCP gives Community and Enterprise 16+ the same power for $0, with the LLM you already use (Claude, GPT, Gemini, DeepSeek, Ollama).<br>
  Five-minute install. Zero Odoo-side setup. Safe writes, real diagnostics, JSON-2 ready years before the Odoo 22 XML-RPC removal.
</p>

<p align="center">
  <a href="https://github.com/castaso/CASTADOO_MCP"><img alt="Repository" src="https://img.shields.io/badge/repo-castaso%2FCASTADOO__MCP-blue.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-black.svg"></a>
  <img alt="Tools" src="https://img.shields.io/badge/tools-41-green.svg">
</p>

> **About this repository.** CASTADOO MCP is a branding and packaging fork of
> [`erpipe-org/mcp-odoo`](https://github.com/erpipe-org/mcp-odoo) (MIT). The
> functional code is upstream's; this fork renames the distribution to
> `castadoo-mcp`, rebrands the visible product surface, and tracks upstream
> continuously. See [NOTICE.md](./NOTICE.md) for the exact scope of changes and
> [SYNCING.md](./SYNCING.md) for how upstream updates are pulled in. The upstream
> copyright notice is retained in [LICENSE](./LICENSE).

CASTADOO MCP turns any Odoo 16+ database into a Model Context Protocol server — using only your existing credentials. **No App Store module, no permission setup, no admin access required.** Built for local agents, IDEs, and automation tools that need real Odoo context without hand-rolled scripts or unsafe direct write access.

It speaks XML-RPC for Odoo 16-18 and External JSON-2 for Odoo 19+. It exposes a compact MCP surface with read tools, diagnostics, schema discovery, migration helpers, local addon scanning, and a gated write workflow. One server can serve [multiple named Odoo instances](#multiple-odoo-instances) at once.

## Try it in 30 seconds

Once configured (see [Setup](#setup)), ask your agent things like:

> "Show me all customers from Spain with unpaid invoices."
>
> "Find products with stock below 10 units in the main warehouse."
>
> "Audit the `custom_billing` addon for upgrade risks before we move to Odoo 19."

> **Note on this fork.** The 41-tool surface, prompts, resources and every
> environment variable are identical to upstream, so upstream documentation and
> community answers apply directly. Only the install command differs:
> `uvx castadoo-mcp` instead of `uvx odoo-mcp`.
