# Publishing runbook — crivo

Updated 2026-08-31 after the rename and the MIT license landing. The old
blocker (license) is resolved; nothing blocks a publish except doing it.
Publishing also *locks the name*: `crivo` on PyPI is first-come, and it is
only provisionally ours until 0.1.0 is uploaded.

## 0. Preconditions (all done)

- [x] License: MIT (`LICENSE` + PEP 639 fields in `pyproject.toml`;
      wheel metadata verified: `License-Expression: MIT`).
- [x] Package name `crivo`, console scripts `crivo` / `crivo-mcp`.
- [x] CI green on Linux + macOS (`.github/workflows/ci.yml`).

Still Aarmen's, before or right after publish:

- [ ] Rename the GitHub repo: Settings > General > Repository name > `crivo`.
      GitHub redirects the old URLs. Then update `[project.urls]` in
      `pyproject.toml` and the README install line + CI badge path.

## 1. Version

`0.1.0` for the first release. Patch (`0.1.x`) for fixes, minor (`0.2.0`)
when `crivo.ask` or the v2 toolkit lands. `version` in `pyproject.toml` is
the only version site.

## 2. Build

```bash
rm -rf dist && uv build
```

Expect `dist/crivo-<version>.tar.gz` and `dist/crivo-<version>-py3-none-any.whl`.

## 3. Verify the name is still free, dry run, publish

```bash
curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/crivo/json   # 404 = still free
```

Create tokens at test.pypi.org and pypi.org (account settings > API tokens).

```bash
uv publish --index testpypi --token "$TEST_PYPI_TOKEN"
uvx --index-url https://test.pypi.org/simple/ crivo diagnose data/sample_sales.csv

uv publish --token "$PYPI_TOKEN"
```

## 4. Flip the README

- Install line becomes `pip install crivo`.
- The MCP config example becomes:

```json
{
  "mcpServers": {
    "crivo": {
      "command": "uvx",
      "args": ["--from", "crivo", "crivo-mcp"],
      "env": { "DEEPSEEK_API_KEY": "sk-..." }
    }
  }
}
```

Keep the from-a-checkout form in a footnote for contributors.

## 5. Registry submissions

Note: the MCP server should be ported to the 2026-07-28 spec (stateless,
MRTR input requests, tasks extension) before pushing registry listings hard —
see the MCP workstream in `specs/2026-08-31-phase6-plus-roadmap.md`.

One blurb, reused everywhere (plain voice, no hype):

> crivo cleans messy tabular data and answers questions over it, shipping
> every change with executed checks and lineage back to the raw file.
> diagnose is free and keyless; fixes count only when the check that found
> the problem re-runs clean; judgement calls are always deferred to a human.

Destinations, in order:

- **Official MCP Registry** (registry.modelcontextprotocol.io): publish a
  `server.json` with namespace verification (GitHub OIDC gives
  `io.github.aarmens702-hub/crivo`). Aggregators (PulseMCP, Glama, Smithery,
  mcp.so) federate from it.
- **modelcontextprotocol/servers**: one-line PR to the community list.
- **Smithery / PulseMCP / Glama** direct submissions as backup; they read the
  repo, so the README is the listing. State the sandbox posture loudly —
  security posture is an adoption gate for MCP servers now.

## 6. Post-publish smoke, on a machine that is not this one

```bash
uvx crivo diagnose some.csv        # detection, no key
uvx --from crivo crivo-mcp         # should start and wait on stdio
```

If both run, add the PyPI version badge to README.md.
