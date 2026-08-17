# Notebook-native output + pandas accessor (Phase 1.2–1.4)

Finishes Phase 1 of `specs/2026-08-15-master-roadmap.md`: make the library feel
native in a notebook. Approach **1′** (agreed 2026-08-16): one presentation
module, one diff helper (my sequential prefix), one accessor; agents produce
self-contained new files only; I own the shared-file wiring at integration.

## What

Three additions to the keyless public surface:

1. **P1.2 — notebook HTML.** `Report._repr_html_` and `CleanSummary._repr_html_`
   so `aa.diagnose(df)` / `aa.clean(df)` render as styled cards in Jupyter, not
   plain text.
2. **P1.4 — before/after diff.** Folded into `CleanSummary`'s HTML (an inline
   `"old" → new` sample per applied fix) plus an opt-in `CleanSummary.diff()`
   returning a highlighted pandas Styler for the full frame.
3. **P1.3 — pandas accessor.** `df.aa.diagnose()` / `df.aa.clean()` as thin
   forwarders to the module functions.

## Design decisions (locked)

- **Self-contained dark card, not theme-adaptive.** The HTML paints its own dark
  ground (the `terminal.svg` identity), so it reads the same on a light or dark
  notebook and does not depend on the host renderer's dark-mode handling. A
  deliberate single-visual-world commitment: paint background and every colour
  explicitly.
- **Identity tokens reused verbatim** from `docs/assets/banner.svg` /
  `terminal.svg`: ground `#14171a` / panel `#1b1f24`, ink `#c7cdd3` / bright
  `#e7e9e4`, muted `#6c7681` / `#8c95a0`, accent teal `#56b9c2` / `#3a8f97`.
  Grades: **AUTO** `#5cb98a` · **GATE** `#d5a24f` · **HUMAN** `#e05a54`. Mono
  stack for data, sans for chrome.
- **All CSS inline on elements.** No `<style>` block and no `@media` query (a
  notebook may strip either). Everything nested under one `aa-card` wrapper.
- **Every data value is `html.escape`-d** before it enters the HTML — findings,
  column names, and before/after cell values all carry user data.
- **The card is a single visual world**, so no dark/light token swap is needed;
  correctness is: legible contrast on the card's own ground.

## Requirements

### R1 — `notebook.report_html(name, frame, result) -> str`  *(Agent A)*
Mirrors `checkup.render_console` structure, in HTML, inside one `aa-card` div:
- Header: `name` (bright, bold) · `{rows} rows × {cols} columns` (muted) ·
  a teal rule.
- Encoding warning line **iff** `frame.attrs.get("encoding")` not in
  `(None, "utf-8-sig", "utf-8")` — amber.
- Summary line: `{fixable} fixable · {flagged} flagged · {clear} signals clear`,
  each count in its grade/accent colour. `fixable = [f for f in findings if not
  f["indicator"]]`, `flagged = [f ... if f["indicator"]]`, `clear =
  result["clear"]`.
- One row per finding: grade-coloured dot · `dNN` (muted, zero-padded) · `slug`
  (grade colour, bold) · `[columns joined by ", " or "whole table"]` (teal) ·
  `evidence` (muted) · grade-note + `confidence {c:.2f}`. Flagged (indicator)
  findings render like `checkup`: "flagged, never auto-fixed", no confidence.
- Footer: `checked and clean: {compact ranges of result["clear"]}` then the
  italic line `22 signals run on every file — absence is a checked claim, not a
  silence`. Use `SIGNALS`/`len(SINGLE_FRAME)` for the count, not a literal.

Finding dict keys available: `disease:int, slug:str, columns:list[str],
evidence:str, grade:str ("AUTO"/"GATE"/"HUMAN"), confidence:float,
indicator:bool`. `result` keys: `findings, clear:list[int], broken:dict`.

### R2 — `notebook.clean_html(summary) -> str`  *(Agent A)*
The "rich" clean output, same `aa-card` shell:
- Header: `cleaned  {len(applied)} applied · {len(needs_review)} deferred`.
- Per applied fix (green): `✓ dNN slug [cols]` then up to 3 `repr(old) → new`
  examples from `summary.samples()` (muted, mono). For a `removed` example
  render `column removed (constant {value!r})`.
- Per deferred fix (muted): `· dNN slug [cols]` + ` — {reason}` when present.
- `applied`/`needs_review` items have keys `disease, slug, columns, grade`
  (+ `reason` on some deferred). Samples come from `summary.samples()` only.

### R3 — diff helper  *(me, sequential prefix — DONE before fan-out)*
In `autoclean.py`:
- `changed_cells(before, after, applied, per_fix=3) -> list[dict]`. For each
  applied fix `{disease, slug, columns}`, collect cells that differ between
  `before` and `after` in those columns (rows align — clean never deletes rows).
  Return `{disease, slug, columns, examples}` where each example is
  `{column, row, old, new}`; for a dropped column (disease 19, column absent in
  `after`) emit `{column, removed: True, value: <constant>}` instead. Cap
  `examples` at `per_fix`. `old`/`new` are the raw cell values (rendering owns
  formatting).
- `CleanSummary.samples()` → `changed_cells(self._before, self._after,
  self.applied)`. This is the only seam Agent A uses; no private attrs leak.
- `styler_diff(before, after, max_rows=200) -> Styler` — highlight changed cells
  green on the aligned common columns, head-capped at `max_rows`. Returns a
  pandas Styler.

### R4 — accessor  *(Agent B)*
`src/analyst_agent/accessor.py`:
```python
@pd.api.extensions.register_dataframe_accessor("aa")
class AnalystAccessor:
    def __init__(self, df): self._df = df
    def diagnose(self, name=None) -> "Report":  # lazy-import aa.diagnose
    def clean(self, policy="auto") -> tuple:     # lazy-import aa.clean
```
Lazy imports inside methods to avoid the `api → autoclean → accessor` cycle.
Registration fires once when `__init__` imports the module.

### R5 — integration  *(me, after fan-out)*
`Report._repr_html_` → `notebook.report_html(self._name, self._frame,
self._result)`. `CleanSummary._repr_html_` → `notebook.clean_html(self)`.
`CleanSummary.diff(max_rows=200)` → `styler_diff(self._before, self._after,
max_rows)`. `__init__.py` imports `accessor`. Export nothing new from `__all__`
except what already belongs (accessor is a side-effect import).

### R6 — docs  *(Agent C)*
README "30 seconds" gains a one-line note that `aa.diagnose`/`aa.clean` render
in notebooks, and the `df.aa` accessor; a short `docs/` recipe showing the
notebook flow. No source changes.

## Acceptance criteria

- `aa.diagnose(df)._repr_html_()` returns a string containing the dataset name,
  at least one finding slug, and the "absence is a checked claim" footer;
  contains **no** `<script>` and **no** `http`/external URL; injected markup in a
  column name or value is escaped.
- `aa.clean(df)` → summary; `summary._repr_html_()` shows a `✓` for an applied
  fix and at least one `→` before/after example.
- `summary.diff()` returns a `pandas.io.formats.style.Styler`.
- `changed_cells` returns correct examples for a numbers-as-strings fix, the
  `removed` shape for a constant-drop, and `[]` when nothing changed.
- `df.aa.diagnose()` equals `aa.diagnose(df)` (same findings); `df.aa.clean()`
  equals `aa.clean(df)`.
- Full suite green, ruff clean. **No Docker** (container tests stay opt-in).

## Delegation map

| Agent | Owns (disjoint, new files) | Depends on |
| --- | --- | --- |
| **prefix (me)** | `autoclean.py` changed_cells/samples/styler_diff + `tests/test_autoclean.py` | — |
| **A** rendering | `notebook.py` + `tests/test_notebook.py` + golden-html script | `summary.samples()`, R1/R2 sigs |
| **B** accessor | `accessor.py` + `tests/test_accessor.py` | `aa.diagnose`/`aa.clean` (shipped) |
| **C** docs | `README.md` + `docs/` recipe | frozen public names |
| **integrate (me)** | `api.py`, `autoclean.py` wiring, `__init__.py` | A + B |

Each agent TDDs its own files red-first (tdd-guard), runs only its own test
file, and touches nothing shared — so integration is drop-in + wiring.

## Research refinements (2026-08-17, from the GitHub deep-dive)

Folded into the agent scopes below. Sources: a study of ydata-profiling's
`_repr_html_`, pandas' accessor/Styler internals, and Strike's onboarding.

**Rendering (Agent A) — the notebook-CSS-bleed defence.** Inline styles make our
card survive GitHub/nbconvert sanitization (ydata's iframe does *not*), but they
give **no isolation *from* the host notebook's CSS**: Jupyter ships
`.jp-RenderedHTMLCommon table/th/td/a {…}` rules and inheritance/`!important` can
bleed *in*. So set `color`, `background`, `font-family`, `font-size`,
`line-height`, `border`, `text-align`, `padding`, and `box-sizing: border-box`
**explicitly on the wrapper AND on every `table`/`th`/`td`/`a`** the card emits —
never rely on inheritance. Add a `max-width` so the card doesn't stretch full
width in VSCode. No `:hover`/`@media`/`@keyframes` (they need a `<style>` block
GitHub strips) — the card is static. `_repr_html_` **returns the string**; never
ydata's `display()` side-effect that returns `None` (it double-renders and
breaks nbconvert/`IPython.display.HTML`). Keep the before/after diff **hand-rolled
HTML**, not a Styler (Styler emits a uuid `<style>` block that GitHub strips and
is slow on big frames).

**Accessor (Agent B) — the non-idempotent-registration guard.** Registration
warns (`UserWarning`) every time `"aa"` pre-exists — double import, `%autoreload`,
tests re-importing. Guard it: `if getattr(pd.DataFrame, "aa", None) is None:`
before registering. Add a `@staticmethod _validate(obj)` and keep `__init__`
**cheap** — pandas re-instantiates the accessor on *every* `df.aa` access (no
caching in current pandas), so `__init__` does validate + store the ref only; all
work happens in `.diagnose()`/`.clean()` with lazy imports.

**Onboarding (Agent C) — the one-liner is the hook.** ydata's proven growth lever
is "one-line EDA like `df.describe()`". Lead the README with `df.aa.diagnose()`
returning a `Report` whose `_repr_html_` *is* the card — immediate visual payoff
in one line. Keep twin entry points (accessor for notebooks, `aa.diagnose()`
function + CLI for scripts/CI) so headless users never trip the accessor
registration. Add a "First 60 seconds" path-to-value block and lead with the
keyless/zero-setup story (we have a genuinely better version than Strike's Echo).

**Rendering hardening (Agent A), from Strike's own CSS/rendering bugs.** Define
**every** CSS token *inline* — never reference a custom property the card didn't
define (Strike shipped `--text`/`--bg` against real `--ink`/`--ground` and got
wrong colors, #1154). Set explicit `color` AND `background` on the root; no
external fonts, no external URLs, no JS (assume `<script>`/`<style>` are stripped,
#1128). "Same content, different environment, silently degraded" is the failure
class (#659) — so the golden-HTML script must be eyeballed in **≥2 renderers**
(JupyterLab + nbconvert at minimum; VSCode if available), not one.

**Import safety (Agents A & B, and integration).** `import analyst_agent` runs in
*every* Jupyter cell that uses `_repr_html_` or `df.aa`, so the import must be
**side-effect-free**: zero network, zero subprocess, zero Docker/kernel probe
(Strike killed its process with an eager startup Seatbelt probe, #1098; and our
standing rule is never to launch Docker on this machine). The accessor's
`__init__.py` side-effect import must stay pure — the notebook/keyless surface
already is; do not let the accessor drag in the agent/kernel path.

**Post-Phase-1 follow-ups (logged in `docs/research-followups-2026-08-17.md`, not
in this build):** `aa.load_example()`; `diagnose --json` with documented exit
codes; a "why not just ydata-profiling / great-expectations" comparison; MCP
stdout-channel test (zero non-protocol bytes on fd 1); versioned + atomically
written provenance/skill/session artifacts; one-function secret redaction across
every egress (incl. saved skills and the HTML card); OpenRouter/OpenAI-compatible
provider env-indirection + offline `list_models() -> []` + `Retry-After`; and —
**propose-only, hand-written core** — content-digest skill governance.

## Priority

P1 (finishes the transformative-install phase; highest adoption leverage).
