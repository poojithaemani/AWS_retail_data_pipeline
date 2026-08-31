-- Phase 1 - format benchmark, table 3 of 4: Parquet, partitioned.
--
-- The combination this project actually uses for curated data, and the one the
-- other three exist to be compared against.
--
-- Two mechanisms are working together here, and it is worth keeping them
-- separate when explaining the result:
--
--   Columnar storage  - a query touching 2 of 6 columns reads 2 columns worth
--                       of bytes. CSV and JSON must read every byte of every
--                       row to find the fields they want.
--   Partition pruning - a WHERE clause on year/month/day eliminates whole
--                       directories before any file is opened. This is not a
--                       Parquet feature; the CSV table prunes too. It is
--                       measured separately for that reason.
--
-- Parquet also carries min/max statistics per row group, so a predicate on a
-- non-partition column can skip row groups entirely. That effect is small at
-- this data size and is not what the benchmark is isolating.

DROP TABLE IF EXISTS bench_orders_parquet;

CREATE EXTERNAL TABLE bench_orders_parquet (
    order_id    string,
    customer_id string,
    product_id  string,
    quantity    int,
    order_date  string,
    status      string
)
PARTITIONED BY (
    year  string,
    month string,
    day   string
)
STORED AS PARQUET
LOCATION 's3://${LAKE}/benchmark/parquet/orders/'
TBLPROPERTIES (
    'parquet.compression' = 'SNAPPY'
);

MSCK REPAIR TABLE bench_orders_parquet;
