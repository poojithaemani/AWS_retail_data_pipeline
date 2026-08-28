# Phase 00 - evidence

Captured 2026-08-28T22:04:38+00:00 from account `749185461065` in `us-east-2`.
Commit `9edad255357f` on `main`  **(working tree dirty)**

> Phase 0 complete. Snapshot taken AFTER teardown: the training layer is intentionally gone, so the Athena workgroup is not found and the lake is empty. The persistent layer (KMS, lake bucket, IAM, budgets) remains by design.

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 0 | 0.0 |
| `processed/` | 0 | 0.0 |
| `curated/` | 0 | 0.0 |
| `quarantine/` | 0 | 0.0 |
| `athena-results/` | 0 | 0.0 |

## Collectors

| collector | result |
| --- | --- |
| `context` | captured |
| `lake` | captured |
| `athena` | not reachable &mdash; WorkGroup is not found. |

## Files

- `athena.json`
- `context.json`
- `lake.json`

