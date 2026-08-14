---
name: fix-whitespace-damage
description: "Strips leading and trailing whitespace from the specified string columns. Use when values contain stray spaces that break exact matching, joins, or validation."
license: "MIT"
compatibility: ">=0.1.0"
metadata:
  disease: "6"
  slug: "whitespace-damage"
  born_from: "data/takehome/hmt-spend-2026-03.csv"
  version: "1"
allowed-tools: []
---
## What this fixes

Strips leading and trailing whitespace from the specified string columns. Use when values contain stray spaces that break exact matching, joins, or validation.

## Where it came from

Disease 6 (whitespace-damage) on `data/takehome/hmt-spend-2026-03.csv`: 8/136 values carry stray whitespace; samples: 'Professional Services '
