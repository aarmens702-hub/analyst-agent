# Publishing runbook

Everything below is ready to execute in about 30 minutes. One decision blocks
it, and it is deliberately not made in code: **the license**. Everything else
— metadata, build, entry points — is already in place and verified
(`uv build` produces a working sdist + wheel; both console scripts ride the
wheel's entry points).

## 0. The one blocker: choose a license

MIT recommended: maximum adoption for a tool whose value is being *used*, and
it matches the ecosystem this borrows patterns from (prime-agent is MIT).

```bash
# after deciding:
curl -s https://raw.githubusercontent.com/licenses/license-templates/master/templates/mit.txt  # or write LICENSE by hand
```

Then in `pyproject.toml` under `[project]`:

```toml
license = "MIT"
license-files = ["LICENSE"]
```

Commit both. Without this step, stop: publishing unlicensed code technically
reserves all rights while inviting use, which is the worst of both.

## 1. Version

`0.1.0` is right for a first release. Bump the patch (`0.1.x`) for fixes, the
minor (`0.2.0`) when the MCP toolkit (v2) lands. Edit `version` in
`pyproject.toml`; there is no other version site.

## 2. Build

```bash
rm -rf dist && uv build
```

Expect `dist/analyst_agent-<version>.tar.gz` and `...-py3-none-any.whl`.

## 3. Dry run on TestPyPI, then publish

Create tokens at test.pypi.org and pypi.org (account settings > API tokens).

```bash
uv publish --index testpypi --token "$TEST_PYPI_TOKEN"   # configure the index once in uv's config
uvx --index-url https://test.pypi.org/simple/ analyst-agent diagnose data/sample_sales.csv

uv publish --token "$PYPI_TOKEN"
```

## 4. Flip the README snippet

After the real publish, the MCP config in README.md changes from the local
form to:

```json
{
  "mcpServers": {
    "analyst-agent": {
      "command": "uvx",
      "args": ["--from", "analyst-agent", "analyst-agent-mcp"],
      "env": { "DEEPSEEK_API_KEY": "sk-..." }
    }
  }
}
```

Keep the `uv --directory` form in a "from a checkout" footnote for
contributors.

## 5. Registry submissions

One blurb, reused everywhere (plain voice, no hype):

> analyst-agent cleans messy tabular data in a sandboxed kernel and answers
> questions over it, shipping every change with executed checks and lineage
> back to the raw file. The MCP server exposes six tools; diagnose_file is
> free and keyless, and judgement-grade fixes are always deferred to a human.

Destinations:

- **modelcontextprotocol/servers** (github.com/modelcontextprotocol/servers):
  PR adding one line to the community servers list in README, alphabetical,
  linking the repo. Follow the existing line format exactly.
- **Smithery** (smithery.ai): submit via their "add server" flow; it reads the
  repo, so the README table is the listing.
- **PulseMCP** (pulsemcp.com) and **Glama** (glama.ai/mcp/servers): both have
  submit-a-server forms; paste the blurb and repo URL.

## 6. Post-publish smoke, on a machine that is not this one

```bash
uvx analyst-agent diagnose some.csv          # detection, no key
uvx --from analyst-agent analyst-agent-mcp   # should start and wait on stdio
```

If both run, update the "Publishing status" note at the bottom of README.md.
