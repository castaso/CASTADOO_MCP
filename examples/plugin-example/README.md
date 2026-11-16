# castadoo-mcp plugin example

Minimal third-party tool plugin for [castadoo-mcp](https://github.com/castaso/CASTADOO_MCP).

```bash
pip install -e .                       # install next to castadoo-mcp
ODOO_MCP_PLUGINS=example castadoo-mcp      # opt in by entry-point name
```

The `example_count_records` tool then appears in `tools/list`, and
`health_check.plugins.loaded` shows `["example"]`.

Authoring guide + security model: [docs/plugins.md](../../docs/plugins.md).
