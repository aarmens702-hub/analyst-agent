---
name: fix-sentinel-missing
description: "Replaces common missing-data sentinel strings (e.g. \"empty\", \"missing\", \"null\", \"n/a\", \"unknown\", \"none\") with NaN in the provided columns. Use when placeholder tokens are masquerading as real values and should be treated as missing."
license: "MIT"
compatibility: ">=0.1.0"
metadata:
  disease: "4"
  slug: "sentinel-missing"
  born_from: "/private/tmp/claude-501/-Users-aarmensidhu-Desktop-analyst-agent/815644db-8276-4c5d-a713-80bc9c39493c/scratchpad/compound/hospital_scores.csv"
  version: "1"
allowed-tools: []
---
## What this fixes

Replaces common missing-data sentinel strings (e.g. "empty", "missing", "null", "n/a", "unknown", "none") with NaN in the provided columns. Use when placeholder tokens are masquerading as real values and should be treated as missing.

## Where it came from

Disease 4 (sentinel-missing) on `/private/tmp/claude-501/-Users-aarmensidhu-Desktop-analyst-agent/815644db-8276-4c5d-a713-80bc9c39493c/scratchpad/compound/hospital_scores.csv`: 14/150 values are missing-data tokens ('empty') masquerading as data
