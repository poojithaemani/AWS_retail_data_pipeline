# Phase 05 - evidence

Captured 2026-09-03T01:20:26+00:00 from account `749185461065` in `us-east-2`.
Commit `26bbd88bc650` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 46 | 1.005 |
| `processed/` | 0 | 0.0 |
| `curated/` | 35 | 0.561 |
| `quarantine/` | 1 | 0.003 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 98 | 0.133 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.221 | 1202 | `SELECT c.country, COUNT(DISTINCT s.customer_id) AS buyers, SUM(s.order_total) AS revenue F` |
| SUCCEEDED | 0.221 | 1443 | `SELECT c.country, COUNT(DISTINCT s.customer_id) AS buyers, SUM(s.order_total) AS revenue F` |
| SUCCEEDED | 0.071 | 911 | `SELECT category, COUNT(*) AS orders, SUM(order_total) AS revenue, ROUND(AVG(order_total), ` |
| SUCCEEDED | 0.0 | 617 | `SELECT rejection_reason, COUNT(*) AS rows_rejected FROM quarantine_sales GROUP BY rejectio` |
| SUCCEEDED | 0.159 | 684 | `SELECT COUNT(*) AS rows_seen, MIN(order_id) AS lo, MAX(status) AS hi, MIN(product_name) AS` |
| SUCCEEDED | 0.001 | 614 | `SELECT COUNT(*) AS orders, SUM(order_total) AS revenue FROM curated_sales WHERE year = '20` |
| SUCCEEDED | 0.0 | 271 | `DROP TABLE IF EXISTS sales_by_category_ctas` |
| SUCCEEDED | 0.001 | 765 | `SELECT category, SUM(revenue) AS revenue FROM sales_by_category_ctas GROUP BY category ORD` |
| SUCCEEDED | 0.159 | 1841 | `SELECT COUNT(*) AS rows_seen, MIN(order_id) AS lo, MAX(status) AS hi, MIN(product_name) AS` |
| SUCCEEDED | 0.0 | 326 | `CREATE EXTERNAL TABLE IF NOT EXISTS curated_sales ( order_id string, customer_id string, p` |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-enable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `jr_1e9b2daf8...` | SUCCEEDED | 60 | 121.0 | 0 | 0 | 0 | 0 | 0 | True |
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

- `athena-analytics.json`
- `athena.json`
- `catalog.json`
- `context.json`
- `data-quality.json`
- `etl.json`
- `lake.json`

