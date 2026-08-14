---
name: fix-numbers-as-strings
description: "Converts numeric columns stored as strings with formatting residue such as thousands commas, currency symbols, or units into a clean numeric dtype. Use when a column contains values like '26,594.25' and should be numeric."
license: "MIT"
compatibility: ">=0.1.0"
metadata:
  disease: "1"
  slug: "numbers-as-strings"
  born_from: "data/takehome/hmt-spend-2026-03.csv"
  version: "1"
allowed-tools: []
---
## What this fixes

Converts numeric columns stored as strings with formatting residue such as thousands commas, currency symbols, or units into a clean numeric dtype. Use when a column contains values like '26,594.25' and should be numeric.

## Where it came from

Disease 1 (numbers-as-strings) on `data/takehome/hmt-spend-2026-03.csv`: 134/136 values carry currency/unit residue; samples: '26,594.25', '37,224.00', '121,485.23'
