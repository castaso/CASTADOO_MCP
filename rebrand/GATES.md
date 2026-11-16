# Gates: CASTADOO MCP fork + conflict-free rebrand

Scope: `castaso/CASTADOO_MCP` fork with pristine `main`, generated `rebrand/CASTADOO_MCP`,
and reproducible tooling on `tooling/rebrand`.

- [x] G1: Fork exists, clone has origin+upstream remotes, `main` is byte-identical to `upstream/main`
  CHECK: python rebrand/verify.py G1
  EXPECT: RESULT: PASS
  EVIDENCE: ok   main carries zero extra commits (got 0) | RESULT: PASS (1 gate(s))

- [x] G2: Generator runs clean on pristine upstream and is idempotent (second run = no change)
  CHECK: python rebrand/verify.py G2
  EXPECT: RESULT: PASS
  EVIDENCE: ok   generator touched 90 path(s) on first run | RESULT: PASS (1 gate(s))

- [x] G3: `rebrand/CASTADOO_MCP` equals upstream/main + exactly one generated commit carrying the marker
  CHECK: python rebrand/verify.py G3
  EXPECT: RESULT: PASS
  EVIDENCE: ok   commit names its upstream base | RESULT: PASS (1 gate(s))

- [x] G4: Zero hosted-product promo outside CHANGELOG history; surviving upstream mentions are provenance links only
  CHECK: python rebrand/verify.py G4
  EXPECT: RESULT: PASS
  EVIDENCE: ok   remaining 'erpipe' mentions are upstream provenance links only | RESULT: PASS (1 gate(s))

- [x] G5: Dist, CLI script and image identity renamed to castadoo-mcp in pyproject, uv.lock, Dockerfile, all manifests
  CHECK: python rebrand/verify.py G5
  EXPECT: RESULT: PASS
  EVIDENCE: ok   smithery.yaml contains command: "castadoo-mcp" | RESULT: PASS (1 gate(s))

- [x] G6: Runtime display name is CASTADOO, banner updated, and the matching test assertion updated
  CHECK: python rebrand/verify.py G6
  EXPECT: RESULT: PASS
  EVIDENCE: ok   test_server.py asserts the new display name | RESULT: PASS (1 gate(s))

- [x] G7: `site/` and all publish workflows deleted; test-only CI workflow present
  CHECK: python rebrand/verify.py G7
  EXPECT: RESULT: PASS
  EVIDENCE: ok   CI has no publish step (smithery) | RESULT: PASS (1 gate(s))

- [x] G8: LICENSE keeps upstream MIT text verbatim and adds the CASTADOO copyright; NOTICE.md and TRADEMARKS.md exist
  CHECK: python rebrand/verify.py G8
  EXPECT: RESULT: PASS
  EVIDENCE: ok   TRADEMARKS.md attributes Odoo S.A. | RESULT: PASS (1 gate(s))

- [x] G9: README.md is CASTADOO-branded, has no hosted-product promo, and documents install as castadoo-mcp
  CHECK: python rebrand/verify.py G9
  EXPECT: RESULT: PASS
  EVIDENCE: ok   README may mention the import package | RESULT: PASS (1 gate(s))

- [x] G10: Full test suite passes on the rebrand tree, allowing only recorded upstream baseline failures
  CHECK: python rebrand/verify.py G10
  EXPECT: RESULT: PASS
  EVIDENCE: ok   failing tests (1) within recorded baseline (2) | RESULT: PASS (1 gate(s))

- [x] G11: `castadoo-mcp --health` and `python -m odoo_mcp --health` both return exit 0 with JSON posture
  CHECK: python rebrand/verify.py G11
  EXPECT: RESULT: PASS
  EVIDENCE: ok   castadoo-mcp --health leaks no credentials | RESULT: PASS (1 gate(s))

- [x] G12: Compatibility contract preserved - env vars, import module, tool names, prompts and odoo:// URIs identical to upstream
  CHECK: python rebrand/verify.py G12
  EXPECT: RESULT: PASS
  EVIDENCE: ok   __init__.py readable | RESULT: PASS (1 gate(s))

- [x] G13: Rebrand tree is bit-reproducible from upstream (tree hash match on fresh regeneration)
  CHECK: python rebrand/verify.py G13
  EXPECT: RESULT: PASS
  EVIDENCE: ok   regenerated tree hash == rebrand/CASTADOO_MCP tree (d32e17cd57 vs d32e17cd57) | RESULT: PASS (1 gate(s))

- [x] G14: pre-push hook installed and refuses a hand-edited rebrand commit
  CHECK: python rebrand/verify.py G14
  EXPECT: RESULT: PASS
  EVIDENCE: ok   hook self-test passes (rejects unmarked commits) | RESULT: PASS (1 gate(s))

- [x] G15: All three branches pushed; fork default branch is rebrand/CASTADOO_MCP
  CHECK: python rebrand/verify.py G15
  EXPECT: RESULT: PASS
  EVIDENCE: ok   fork default branch = rebrand/CASTADOO_MCP (got rebrand/CASTADOO_MCP) | RESULT: PASS (1 gate(s))

- [x] G16: Upstream sync path proven end to end (ff main + regenerate + verify + force push)
  CHECK: python rebrand/verify.py G16
  EXPECT: RESULT: PASS
  EVIDENCE: ok   sync plan reseats the rebrand branch instead of merging | RESULT: PASS (1 gate(s))

- [x] G17: Authored assets are copied verbatim, not mangled by the identity pass
  CHECK: python rebrand/verify.py G17
  EXPECT: RESULT: PASS
  EVIDENCE: ok   NOTICE.md states this fork's own URL | RESULT: PASS (1 gate(s))

<!--
Rules:
- Boxes flipped by gate-check.mjs when CHECK output matches EXPECT.
- A checked box whose EVIDENCE still reads "pending" counts as UNMET.
- If a gate becomes impossible: add "ABANDON: G<n> <reason>" and report it.
-->
