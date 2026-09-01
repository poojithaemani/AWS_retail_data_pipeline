# Phase 02 - evidence

Captured 2026-09-01T22:26:08+00:00 from account `749185461065` in `us-east-2`.
Commit `1ec899c41cf3` on `main`  **(working tree dirty)**

> Phase 2: Glue Catalog and Crawlers. Crawler discovers schema; publish_catalog.py republishes under the brief's required *_raw names (Glue cannot produce them - verified against the Developer Guide). Five failures encountered and documented: header inference on all-string columns, a custom classifier that did not fix it and corrupted another table, CombineCompatibleSchemas suppressing the drift table, an IAM denial the crawler reported as SUCCEEDED, and a classifier reference Terraform could not clear by update. Schema drift demonstrated and restored; raw never modified.

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 44 | 0.882 |
| `processed/` | 0 | 0.0 |
| `curated/` | 0 | 0.0 |
| `quarantine/` | 0 | 0.0 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 150 | 0.263 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| FAILED | 0.0 | 427 | `SELECT customer_id, customer_name, country FROM customers_raw ORDER BY customer_id LIMIT 1` |
| SUCCEEDED | 0.0 | 532 | `SELECT round(sum(price),2) FROM products_csv` |
| SUCCEEDED | 0.882 | 1072 | `SELECT c.country, round(sum(o.quantity * p.price), 2) AS revenue FROM orders_raw o JOIN cu` |
| SUCCEEDED | 0.066 | 550 | `SELECT * FROM customers_raw LIMIT 2` |
| SUCCEEDED | 0.009 | 581 | `SELECT count(*) AS n FROM products_raw` |
| SUCCEEDED | 0.009 | 540 | `SELECT round(sum(price),2) FROM products_raw` |
| FAILED | 0.0 | 266 | `SELECT round(sum(o.quantity*p.price),2) FROM orders_raw o JOIN products_20260901_csv p ON ` |
| SUCCEEDED | 0.009 | 535 | `SELECT count(*) AS n, round(avg(price),2) AS avg_price FROM products_raw` |
| SUCCEEDED | 0.066 | 680 | `SELECT count(*) AS n FROM customers_raw` |
| SUCCEEDED | 0.807 | 824 | `SELECT count(*) AS n FROM orders_raw` |

## Collectors

| collector | result |
| --- | --- |
| `context` | captured |
| `lake` | captured |
| `athena` | captured |
| `catalog` | captured |

## Files

- `athena.json`
- `catalog-audit.json`
- `catalog.json`
- `context.json`
- `drift-queries.json`
- `lake.json`

