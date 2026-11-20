# Archived: routing benchmark v1 (106 cases)

Retired 2026-11-18. Do not edit. Kept so earlier numbers remain attributable.

## What this was

106 next-action routing cases for the `bodhan_tool_agent`, split 64 train / 21 dev /
21 test, covering 11 MCP tools plus `submit_plan` and `finish`.

Frozen dataset SHA-256:

```
881c6d8953a7287b22d8096735f1de3b750a9a8ed1d7bbf049c86138894ab3b3
```

`validation_report.json` in this folder records that hash and the split counts.

## Why it was retired

1. **The catalog shrank.** `chunk_audio` was deleted from the MCP server, and
   `get_cache_stats` / `clear_cache` were removed from the model-facing catalog. Thirteen
   cases routed to tools the agent can no longer call.
2. **The split was stratified by tool, not by decision type.** The held-out set ended up
   with 12 of 14 `argument_edge` cases and **zero** `near_neighbor_confusion`,
   `multilingual_script` or `single_tool_happy` cases. The reported score was effectively
   an argument-edge score.
3. **The train split taught almost nothing about routing.** It contained 20 happy-path
   single-tool cases and exactly one pipeline case.
4. **It was too large for what it measured.** 21 test cases with one skewed category mix
   gave false precision: one case moved the headline number by 4.8 points.

## Where v1 survives

`routing_cases*.jsonl`, `validation_report.json`, `_swarm/` (raw writer slices), the v1
`README.md`, and the 11-tool `tool_catalog_snapshot.json` captured from the last notebook
run. The snapshot here predates the catalog change; the live snapshot lives in
`experiments/data/tool_catalog_snapshot.json`.

## What replaced it

35 hand-authored cases built by `experiments/scripts/build_routing_dataset.py` against the
8-tool model-facing catalog. Each case names the failure mode it catches. See
`experiments/data/README.md`.
