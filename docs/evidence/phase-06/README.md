# Phase 06 - evidence

Captured 2026-09-04T21:34:15+00:00 from account `749185461065` in `us-east-2`.
Commit `f453a5cb7d4a` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 46 | 1.005 |
| `processed/` | 0 | 0.0 |
| `curated/` | 35 | 0.561 |
| `quarantine/` | 1 | 0.003 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 146 | 0.03 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.066 | 723 | `SELECT * FROM customers_raw LIMIT 3` |
| SUCCEEDED | 0.066 | 792 | `SELECT customer_id, country FROM customers_raw LIMIT 5` |
| FAILED | 0.0 | 340 | `SELECT COUNT(*) FROM customers_raw` |
| SUCCEEDED | 0.066 | 751 | `SELECT COUNT(*) FROM customers_raw` |
| SUCCEEDED | 0.066 | 610 | `SELECT * FROM customers_raw LIMIT 3` |
| FAILED | 0.0 | 422 | `SELECT COUNT(*) FROM orders_raw` |
| FAILED | 0.0 | 479 | `SELECT email FROM customers_raw LIMIT 5` |
| SUCCEEDED | 0.066 | 703 | `SELECT * FROM customers_raw LIMIT 5` |
| SUCCEEDED | 0.066 | 654 | `SELECT * FROM customers_raw LIMIT 5` |
| SUCCEEDED | 0.93 | 991 | `SELECT COUNT(*) FROM orders_raw` |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-enable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `jr_7dae49c3d...` | SUCCEEDED | 129 | 259.0 | 15861 | 3761 | 12060 | 40 | 12060 | True |
| `jr_be7c7d7c5...` | SUCCEEDED | 77 | 155.0 | 0 | 0 | 0 | 0 | 0 | True |
| `jr_60ea25ad2...` | SUCCEEDED | 100 | 201.0 | 15861 | 3761 | 12060 | 40 | 12060 | True |
| `jr_ed752986b...` | FAILED | 75 | 150.0 | - | - | - | - | - | - |
| `jr_1f86f00e0...` | FAILED | 65 | 130.0 | - | - | - | - | - | - |

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
- `persona-verification.json`

