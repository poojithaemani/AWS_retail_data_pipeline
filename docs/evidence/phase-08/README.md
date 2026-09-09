# Phase 08 - evidence

Captured 2026-09-09T23:06:01+00:00 from account `749185461065` in `us-east-2`.
Commit `eadbee2e8537` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 47 | 1.008 |
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
| `jr_31093715c...` | SUCCEEDED | 85 | 170.0 | 0 | 0 | 0 | 0 | 0 | True |
| `jr_e0ce5aadf...` | FAILED | 52 | 104.0 | - | - | - | - | - | - |
| `jr_8b2f2ddc5...` | SUCCEEDED | 98 | 196.0 | 15861 | 3761 | 12060 | 40 | 12060 | True |

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
- `orchestration.json`

