# Phase 07 - evidence

Captured 2026-09-09T18:48:27+00:00 from account `749185461065` in `us-east-2`.
Commit `4231719d9b80` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 46 | 1.005 |
| `processed/` | 0 | 0.0 |
| `curated/` | 35 | 0.561 |
| `quarantine/` | 1 | 0.003 |
| `benchmark/` | 0 | 0.0 |
| `experiments/` | 0 | 0.0 |
| `athena-results/` | 0 | 0.0 |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-enable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `jr_5855fd478...` | SUCCEEDED | 106 | 212.0 | 15861 | 3761 | 12060 | 40 | 12060 | True |

## Collectors

| collector | result |
| --- | --- |
| `context` | captured |
| `lake` | captured |
| `athena` | captured |
| `catalog` | captured |
| `etl` | captured |
| `governance` | captured |

## Files

- `athena.json`
- `catalog.json`
- `context.json`
- `etl.json`
- `governance.json`
- `lake.json`
- `warehouse.json`

