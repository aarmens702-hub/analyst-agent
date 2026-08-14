---
name: fix-empty-constant-columns
description: "Drops columns that hold a single repeated value (including all-null columns), since they carry no information. Use when a detector flags a column with one or fewer distinct values."
license: "MIT"
compatibility: ">=0.1.0"
metadata:
  disease: "19"
  slug: "empty-constant-columns"
  born_from: "data/takehome/hmt-spend-2026-03.csv"
  version: "1"
allowed-tools: []
---
## What this fixes

Drops columns that hold a single repeated value (including all-null columns), since they carry no information. Use when a detector flags a column with one or fewer distinct values.

## Where it came from

Disease 19 (empty-constant-columns) on `data/takehome/hmt-spend-2026-03.csv`: column holds one value ('HM Treasury') in every row — it carries no information
