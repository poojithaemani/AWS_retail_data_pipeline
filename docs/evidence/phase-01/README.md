# Phase 01 - evidence

Captured 2026-08-30T23:18:46+00:00 from account `749185461065` in `us-east-2`.
Commit `603db41c1779` on `main`  **(working tree dirty)**

> Phase 1: raw immutability enforced (IAM split + bucket deny), four hand-written benchmark tables, format comparison complete. Training layer UP at capture time.

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 32 | 0.662 |
| `processed/` | 0 | 0.0 |
| `curated/` | 0 | 0.0 |
| `quarantine/` | 0 | 0.0 |
| `benchmark/` | 97 | 2.853 |
| `athena-results/` | 10 | 0.001 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.004 | 626 | `SELECT sum(quantity) AS units FROM bench_orders_parquet_flat` |
| SUCCEEDED | 1.415 | 763 | `SELECT sum(quantity) AS units FROM bench_orders_json` |
| SUCCEEDED | 0.004 | 519 | `SELECT sum(quantity) AS units FROM bench_orders_parquet_flat` |
| SUCCEEDED | 0.016 | 536 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_parquet_flat W` |
| SUCCEEDED | 0.063 | 1114 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_json WHERE yea` |
| SUCCEEDED | 0.015 | 708 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_parquet GROUP ` |
| SUCCEEDED | 0.008 | 480 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_parquet_flat G` |
| SUCCEEDED | 0.008 | 488 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_parquet_flat G` |
| SUCCEEDED | 0.015 | 931 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_parquet GROUP ` |
| SUCCEEDED | 1.415 | 758 | `SELECT status, count(*) AS orders, sum(quantity) AS units FROM bench_orders_json GROUP BY ` |

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
- `lake.json`

