# Phase 03 - evidence (CLEAN BASELINE, before the orphan delivery)

This is the state captured *before* the deliberate orphan-product defect was
delivered into `raw/`, kept because the delivery is a permanent append and the
clean lake cannot be reconstructed once it lands.

Read it against the capture one directory up, which is the same lake after the
defect. The pair is the exercise: 10,000 curated rows and $8,850,092.78 here,
10,060 and $8,911,095.39 there, with 40 rows quarantined rather than lost.

---

Captured 2026-09-02T04:31:48+00:00 from account `749185461065` in `us-east-2`.
Commit `bb10cde799ef` on `main`  **(working tree dirty)**

## Lake layers

| prefix | objects | MB |
| --- | ---: | ---: |
| `raw/` | 44 | 0.882 |
| `processed/` | 0 | 0.0 |
| `curated/` | 32 | 0.498 |
| `quarantine/` | 1 | 0.002 |
| `benchmark/` | 133 | 3.741 |
| `experiments/` | 952 | 24.228 |
| `athena-results/` | 158 | 0.265 |

## Athena queries

| state | MB scanned | ms | sql |
| --- | ---: | ---: | --- |
| SUCCEEDED | 0.807 | 968 | `SELECT COUNT(*) AS total_rows, COUNT(DISTINCT order_id) AS distinct_order_ids, SUM(CASE WH` |
| SUCCEEDED | 0.807 | 1003 | `SELECT COUNT(DISTINCT year\|\|'-'\|\|month\|\|'-'\|\|day) AS raw_partitions, MIN(year\|\|'-'\|\|month\|` |
| SUCCEEDED | 0.807 | 817 | `SELECT order_id, COUNT(*) AS occurrences, MIN(order_date) AS earliest, MAX(order_date) AS ` |
| SUCCEEDED | 0.807 | 714 | `SELECT substr(latest,1,7) AS latest_month, COUNT(*) AS order_ids FROM (SELECT order_id, MA` |

## Glue ETL runs

`de-training-curated-sales` &mdash; Glue 5.0, 2 x G.1X, bookmarks `job-bookmark-disable`

| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
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

