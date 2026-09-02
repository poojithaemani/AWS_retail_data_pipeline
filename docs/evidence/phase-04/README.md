# Phase 04 - evidence

Captured 2026-09-02T20:58:18+00:00 from account `749185461065` in `us-east-2`.
Commit `fe3042decf59` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 46 | 1.005 |
| `processed/` | 0 | 0.0 |
| `curated/` | 35 | 0.561 |
| `quarantine/` | 1 | 0.003 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 51 | 0.118 |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-enable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `jr_83b48b484...` | SUCCEEDED | 60 | 120.0 | 0 | 0 | 0 | 0 | 0 | True |
| `jr_ef8cfb840...` | FAILED | 61 | 123.0 | - | - | - | - | - | - |
| `jr_cf5449de0...` | SUCCEEDED | 97 | 194.0 | 2000 | 0 | 2000 | 0 | 2000 | True |
| `jr_adf5f6339...` | SUCCEEDED | 104 | 209.0 | 13861 | 3761 | 10060 | 40 | 10060 | True |

## Collectors

| collector | result |
| --- | --- |
| `context` | captured |
| `lake` | captured |
| `athena` | captured |
| `catalog` | captured |
| `etl` | captured |

## Files

- `athena.json`
- `catalog.json`
- `context.json`
- `etl.json`
- `lake.json`

