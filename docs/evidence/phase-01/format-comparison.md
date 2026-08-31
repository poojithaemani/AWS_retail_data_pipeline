# Phase 1 - format comparison

Database `training_db`, workgroup `de-training-wg`, region `us-east-2`.
Captured 2026-08-31T00:51:31Z.

All four tables describe the **same rows**. Only the encoding and the
partition layout differ.

## On disk

| format | objects | MB | vs CSV |
| --- | ---: | ---: | ---: |
| csv | 42 | 0.807 | 100.0% |
| json | 42 | 1.947 | 241.3% |
| parquet_partitioned | 42 | 0.494 | 61.2% |
| parquet_flat | 1 | 0.218 | 27.1% |

## Query: `pruned_single_day`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.015 | 100.00% | 122 | 502 |
| json | 0.035 | 241.11% | 159 | 514 |
| parquet_partitioned | 0.000 | 3.08% | 117 | 400 |
| parquet_flat | 0.015 | 99.47% | 88 | 432 |

## Query: `full_scan_no_filter`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.807 | 100.00% | 382 | 873 |
| json | 1.947 | 241.25% | 261 | 667 |
| parquet_partitioned | 0.021 | 2.63% | 291 | 678 |
| parquet_flat | 0.008 | 0.96% | 58 | 386 |

## Query: `single_column_aggregate`

| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |
| --- | ---: | ---: | ---: | ---: |
| csv | 0.807 | 100.00% | 454 | 936 |
| json | 1.947 | 241.25% | 423 | 1081 |
| parquet_partitioned | 0.010 | 1.30% | 375 | 1353 |
| parquet_flat | 0.004 | 0.48% | 57 | 338 |

## The three effects, isolated

| effect | comparison | result |
| --- | --- | ---: |
| columnar storage alone | flat Parquet vs CSV, both full scans | **0.96%** |
| partition pruning alone | partitioned vs flat Parquet, both filtered | **3.10%** |
| pruning on a row format | CSV filtered vs CSV full scan | **1.81%** |
| both together | partitioned Parquet vs CSV, both filtered | **3.08%** |

The third row is the one usually left out. CSV prunes too - pruning is a
property of the partition layout, not of Parquet. Quoting only the last
row credits the file format with a saving the directory structure
produced.

### Filtering the flat Parquet table made it scan MORE

`pruned_single_day` scanned **14,527 bytes**; `full_scan_no_filter` scanned **7,778**. The filtered query read **1.9x more data**.

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

