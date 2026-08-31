# Phase 1 - partitioning and file-count experiments

Captured 2026-08-31T01:02:09Z. Database `training_db`, workgroup `de-training-wg`.

500,000 orders across 30 days, arranged four ways. Same rows, same schema, same queries - only the layout on S3 differs.

Timings are the **median of three runs**, with every run listed so a
reader can see the spread. Athena carries roughly a second of fixed
overhead per query; a difference smaller than that is not a claim worth
making, and is reported as inconclusive rather than dressed up.

---

## 1.5 Over-partitioning: 30 partitions vs 720

Identical rows. The hourly layout splits each day into 24, which is the
shape people reach for when they assume more partitions is more pruning.

| | daily (30) | hourly (720) |
| --- | ---: | ---: |
| partitions | 30 | 720 |
| total MB | 9.059 | 13.441 |
| **avg file size (KB)** | 294.9 | 18.23 |

| MSCK REPAIR wall (s) | 4.53 | 18.97 |

**Query: one day** - the case the partitioning is supposed to help.

| | daily | hourly |
| --- | ---: | ---: |
| planning ms (median) | 122 | 359 |
| planning ms (each run) | [120, 214, 122] | [359, 305, 360] |
| total ms (median) | 623 | 957 |
| bytes scanned | 16051 | 21902 |

**Query: full scan** - no filter, so nothing can be pruned.

| | daily | hourly |
| --- | ---: | ---: |
| planning ms (median) | 275 | 922 |
| total ms (median) | 884 | 1683 |
| bytes scanned | 384336 | 563561 |

---

## 1.6 Small files: 1 file vs 200, identical bytes

One day of orders, written twice. Same rows, same total size, same
query. The only variable is how many objects the engine must open.

| | one file | 200 files |
| --- | ---: | ---: |
| files | 1 | 201 |
| total MB | 0.375 | 1.353 |
| avg file size (KB) | 365.74 | 6.57 |
| planning ms (median) | 67 | 84 |
| total ms (median) | 559 | 701 |
| total ms (each run) | [1138, 530, 559] | [701, 883, 668] |
| bytes scanned | 16051 | 65845 |

