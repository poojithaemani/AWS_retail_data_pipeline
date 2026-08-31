# Phase 01 - evidence

Captured 2026-08-31T16:49:13+00:00 from account `749185461065` in `us-east-2`.
Commit `4c40d5e3cb26` on `main`  **(working tree dirty)**

> Phase 1 complete. Raw immutability enforced by IAM split plus bucket-policy deny, verified by policy simulation. Four hand-written benchmark tables (no crawlers - Phase 2). Format comparison: partitioned Parquet scans 2.25% of CSV. Over-partitioning measured at 30 vs 720 partitions; small-file storage penalty conclusive, runtime inconclusive. Captured with the training layer UP.

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 44 | 0.882 |
| `processed/` | 0 | 0.0 |
| `curated/` | 0 | 0.0 |
| `quarantine/` | 0 | 0.0 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 118 | 0.261 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.564 | 1683 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_hourly G` |
| SUCCEEDED | 0.022 | 835 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_hourly W` |
| SUCCEEDED | 0.066 | 668 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_many_fil` |
| SUCCEEDED | 0.564 | 1337 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_hourly G` |
| SUCCEEDED | 0.016 | 1351 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_daily WH` |
| SUCCEEDED | 0.022 | 957 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_hourly W` |
| SUCCEEDED | 0.066 | 883 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_many_fil` |
| SUCCEEDED | 0.016 | 623 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_daily WH` |
| SUCCEEDED | 0.0 | 18277 | `MSCK REPAIR TABLE experiments_orders_hourly` |
| SUCCEEDED | 0.384 | 884 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM experiments_orders_daily GR` |

## Collectors

| collector | result |
| --- | --- |
| `context` | captured |
| `lake` | captured |
| `athena` | captured |
| `catalog` | captured |

## Files

- `athena.json`
- `benchmark.json`
- `catalog.json`
- `context.json`
- `experiments-corpus.json`
- `experiments.json`
- `format-comparison.md`
- `lake.json`
- `partitioning-and-small-files.md`

