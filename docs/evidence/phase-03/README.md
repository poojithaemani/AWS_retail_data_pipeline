# Phase 03 - evidence

Captured 2026-09-02T04:48:12+00:00 from account `749185461065` in `us-east-2`.
Commit `bb10cde799ef` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 45 | 0.888 |
| `processed/` | 0 | 0.0 |
| `curated/` | 33 | 0.504 |
| `quarantine/` | 1 | 0.003 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 166 | 0.265 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.813 | 859 | `SELECT COUNT(*) AS raw_rows, COUNT(DISTINCT order_id) AS distinct_ids, COUNT(DISTINCT year` |
| SUCCEEDED | 0.015 | 1085 | `SELECT COUNT(*) AS rows_kept, SUM(o.quantity * p.price) AS revenue FROM orders_raw o JOIN ` |
| SUCCEEDED | 0.822 | 784 | `SELECT COUNT(*) AS orphan_rows, COUNT(DISTINCT o.product_id) AS orphan_products FROM order` |
| SUCCEEDED | 0.807 | 968 | `SELECT COUNT(*) AS total_rows, COUNT(DISTINCT order_id) AS distinct_order_ids, SUM(CASE WH` |
| SUCCEEDED | 0.807 | 1003 | `SELECT COUNT(DISTINCT year\|\|'-'\|\|month\|\|'-'\|\|day) AS raw_partitions, MIN(year\|\|'-'\|\|month\|` |
| SUCCEEDED | 0.807 | 817 | `SELECT order_id, COUNT(*) AS occurrences, MIN(order_date) AS earliest, MAX(order_date) AS ` |
| SUCCEEDED | 0.807 | 714 | `SELECT substr(latest,1,7) AS latest_month, COUNT(*) AS order_ids FROM (SELECT order_id, MA` |
| SUCCEEDED | 0.015 | 759 | `SELECT COUNT(*) AS rows_kept, SUM(o.quantity * p.price) AS revenue, SUM(CASE WHEN p.produc` |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-disable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `jr_7b55968b2...` | SUCCEEDED | 158 | 316.0 | 13904 | 3803 | 10060 | 41 | 10060 | True |
| `jr_df2808b5e...` | SUCCEEDED | 155 | 310.0 | 13803 | 3802 | 10000 | 1 | 10000 | True |
| `jr_5321217b8...` | FAILED | 142 | 284.0 | 13803 | - | 10000 | 1 | 10000 | False |
| `jr_0f9b3e278...` | FAILED | 118 | 236.0 | - | - | - | - | - | - |
| `jr_9c76e737b...` | FAILED | 46 | 92.0 | - | - | - | - | - | - |

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

