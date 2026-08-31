-- Phase 1 - format benchmark, table 4 of 4: Parquet, UNPARTITIONED.
--
-- The control. Without it the comparison cannot separate two effects that are
-- easy to conflate:
--
--   partitioned Parquet vs CSV   - columnar storage AND pruning together
--   flat Parquet vs CSV          - columnar storage alone
--   partitioned vs flat Parquet  - pruning alone
--
-- Reporting only the first number credits Parquet with a saving that
-- partitioning produced, which is the usual way this comparison gets quoted
-- wrongly.
--
-- This was originally written as a CTAS, which would also have demonstrated a
-- technique the brief lists for Day 5. It does not work here, and the reason is
-- worth keeping: the Athena workgroup sets enforce_workgroup_configuration,
-- which forces a single central output location and rejects any query carrying
-- its own external_location. That setting is what makes the workgroup's 1 GiB
-- per-query scan ceiling a real limit rather than a suggestion a client can
-- override, so the benchmark was changed instead of the guardrail.
--
-- The flat file is produced by the generator (src/generate) and uploaded to
-- benchmark/parquet_flat/, so this is an ordinary external table.
--
-- Note the schema: year, month and day are ordinary string columns here, not
-- partitions. Queries can still filter on them; the filter simply cannot skip
-- any files, because there are no partition directories to skip.

DROP TABLE IF EXISTS bench_orders_parquet_flat;

CREATE EXTERNAL TABLE bench_orders_parquet_flat (
    order_id    string,
    customer_id string,
    product_id  string,
    quantity    int,
    order_date  string,
    status      string,
    year        string,
    month       string,
    day         string
)
STORED AS PARQUET
LOCATION 's3://${LAKE}/benchmark/parquet_flat/orders/'
TBLPROPERTIES (
    'parquet.compression' = 'SNAPPY'
);
