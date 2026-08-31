# Phase 1 - format comparison

Database `training_db`, workgroup `de-training-wg`, region `us-east-2`.
Captured 2026-08-28T23:41:29Z.

All four tables describe the **same rows**. Only the encoding and the
partition layout differ.

## On disk

| format | objects | MB | vs CSV |
| --- | ---: | ---: | ---: |
| csv | 30 | 0.586 | 100.0% |
| json | 30 | 1.415 | 241.3% |
| parquet_partitioned | 30 | 0.356 | 60.8% |
| parquet_flat | 1 | 0.220 | 37.5% |

## Query: `pruned_single_day`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.026 | 100.00% | 110 | 422 |
| json | 0.063 | 241.45% | None | 778 |
| parquet_partitioned | 0.001 | 2.25% | 110 | 467 |
| parquet_flat | 0.016 | 60.87% | 80 | 410 |

## Query: `full_scan_no_filter`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.586 | 100.00% | 312 | 703 |
| json | 1.415 | 241.26% | 362 | 742 |
| parquet_partitioned | 0.015 | 2.60% | 388 | 818 |
| parquet_flat | 0.008 | 1.33% | 61 | 359 |

## Query: `single_column_aggregate`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.586 | 100.00% | 268 | 579 |
| json | 1.415 | 241.26% | 271 | 605 |
| parquet_partitioned | 0.008 | 1.28% | 234 | 540 |
| parquet_flat | 0.004 | 0.66% | 64 | 485 |

## The three effects, isolated

| effect | comparison | result |
| --- | --- | ---: |
| columnar storage alone | flat Parquet vs CSV, both full scans | **1.33%** |
| partition pruning alone | partitioned vs flat Parquet, both filtered | **3.70%** |
| pruning on a row format | CSV filtered vs CSV full scan | **4.46%** |
| both together | partitioned Parquet vs CSV, both filtered | **2.25%** |

The third row is the one usually left out. CSV prunes too - pruning is a
property of the partition layout, not of Parquet. Quoting only the last
row credits the file format with a saving the directory structure
produced.

### Filtering the flat Parquet table made it scan MORE

`pruned_single_day` scanned **15,906 bytes**; `full_scan_no_filter` scanned **7,778**. The filtered query read **2.0x more data**.

This is not an error, and it is the clearest demonstration of columnar
behaviour in the whole comparison. In the flat table `year`, `month`
and `day` are ordinary columns, so:

- `full_scan_no_filter` reads 2 columns: `status`, `quantity`
- `pruned_single_day` reads 5: those two plus the three it filters on

A columnar engine only reads the columns a query names. Adding a
predicate on a column you were not otherwise reading therefore *costs*
bytes. The same predicate against the partitioned table costs nothing,
because the values live in the S3 prefix rather than in the file - the
engine skips whole directories without opening anything.

The practical rule: partition on the columns you filter by, and the
filter becomes free. Leave them as data columns and every filter is a
read.

## Reading these numbers

- **Bytes scanned is actual, not billed.** Athena bills a 10 MB minimum
  per query, so at this data size every row above costs the same. The
  ratio generalises to production volumes; the cost does not.
- **`parquet_flat` vs `csv`** isolates columnar storage: same scan scope,
  different encoding.
- **`parquet_partitioned` vs `parquet_flat`** isolates partition pruning:
  same encoding, different scan scope.
- **`pruned_single_day` vs `full_scan_no_filter`** on the same table shows
  what the WHERE clause is worth. Note that CSV prunes too - pruning is a
  property of the partition layout, not of Parquet.
- Runtime at this scale is dominated by roughly a second of fixed Athena
  overhead. Treat the millisecond columns as indicative only.

