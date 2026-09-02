# Proving Ground results (smoke, 2026-09-02)

All numbers are the **deterministic mode baseline** — keyless
`crivo.clean()`, no LLM. Diseases without a deterministic fixer are
scored "not attempted", never blended into the fixable aggregate.

- datasets: 23 synthetic (7 fully fixable, 5 with repair defined) + 4 external
- detection micro-F1, mean incl. silence-as-zero: 0.581 — silent on 8/23 datasets
- repair F1, repair-defined fixable datasets (5/7): 0.936
- survived-verification rate, mean: 1.000

— means undefined: no fixer attempted, the detector produced nothing to score, or a 0/0 ratio. A fixable dataset can score zero repairs by design (sentinel-clearing and constant-drops land on NaN/removal, never the truth value).

## Synthetic corpus

| dataset | diseases | detect µF1 | dirt F1 | repair F1 | survived |
|---|---|---|---|---|---|
| tx-money-strings | 1 | 1.000 | 1.000 | 0.956 | 1.000 |
| tx-dates-frozen | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| tx-dates-mixed | 3 | 1.000 | — | — | — |
| tx-sentinels | 4 | 0.667 | 0.780 | — | 1.000 |
| tx-suppression | 5 | — | 1.000 | — | 1.000 |
| tx-whitespace | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| tx-case-variants | 7 | — | 0.780 | 0.780 | 1.000 |
| tx-mojibake | 8 | 1.000 | — | — | — |
| tx-dup-rows | 9 | 1.000 | — | — | — |
| tx-near-dups | 10 | — | — | — | — |
| tx-key-violations | 11 | 1.000 | — | — | — |
| pairs-contradictions | 12 | — | — | — | — |
| tx-out-of-domain | 13 | 1.000 | — | — | — |
| geo-broken-coords | 14 | 0.400 | — | — | — |
| tx-outliers | 15 | 1.000 | — | — | — |
| tx-unit-mix | 16 | — | — | — | — |
| tx-packed-fields | 17 | — | — | — | — |
| tx-header-damage | 18 | — | 1.000 | — | — |
| tx-constant-col | 19 | 1.000 | 0.998 | — | 1.000 |
| tx-aggregate-row | 21 | — | — | — | — |
| tx-excel-ids | 22 | 1.000 | — | — | — |
| tx-compound-fixable | 4,6,1,2 | 0.889 | 0.993 | 0.942 | 1.000 |
| tx-compound-hard | 7,15,9,21,18 | 0.400 | 0.952 | — | 1.000 |

## External (Raha) datasets

External dirt we did not design — scored in string space, disease
taxonomy unknown, so detection columns don't apply.

| dataset | dirt F1 | repair F1 | survived |
|---|---|---|---|
| hospital | 0.024 | — | 1.000 |
| flights | 0.431 | — | 1.000 |
| beers | 0.970 | 0.970 | 1.000 |
| rayyan | 0.740 | — | 1.000 |
