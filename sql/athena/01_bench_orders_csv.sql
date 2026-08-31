-- Phase 1 - format benchmark, table 1 of 4: CSV, partitioned.
--
-- Written by hand rather than discovered by a crawler, deliberately. Crawlers
-- arrive in Phase 2; declaring the schema yourself first is what makes
-- schema-on-read concrete, and it means that when the Phase 2 crawler infers
-- something different -- price retyped from double to string because one row
-- says UNKNOWN -- the difference between what you declared and what it
-- inferred is visible rather than theoretical.
--
-- Nothing here touches the data. The files were written once by the loader;
-- this only tells Athena how to read the bytes already sitting in S3. That is
-- the whole idea of schema-on-read, and it is why four tables can describe the
-- same logical dataset four different ways in this directory.
--
-- SerDe choice: LazySimpleSerDe rather than OpenCSVSerde. OpenCSVSerde returns
-- every column as a string, which would make the comparison against Parquet
-- dishonest -- Parquet stores real types, so the CSV table must declare them
-- too or the benchmark measures parsing differences rather than format
-- differences. The generated data has no embedded commas or quotes, so the
-- simpler SerDe is safe here; it would not be on arbitrary CSV.

DROP TABLE IF EXISTS bench_orders_csv;

CREATE EXTERNAL TABLE bench_orders_csv (
    order_id    string,
    customer_id string,
    product_id  string,
    quantity    int,
    order_date  string,
    status      string
)
PARTITIONED BY (
    -- Strings, not integers: the directories are zero-padded (month=07), and
    -- an int partition column would drop the padding and stop matching.
    year  string,
    month string,
    day   string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
WITH SERDEPROPERTIES (
    'field.delim' = ','
)
STORED AS TEXTFILE
LOCATION 's3://${LAKE}/benchmark/csv/orders/'
TBLPROPERTIES (
    'skip.header.line.count' = '1'
);

-- Partitions exist as directories in S3 but not yet in the catalog. Until
-- they are registered, this table returns zero rows -- a partitioned table
-- knows nothing about data it has not been told about.
--
-- MSCK REPAIR lists S3 and registers what it finds. The alternative is
-- partition projection, which computes partition locations from a template and
-- makes no S3 metadata calls at all; it scales better on thousands of
-- partitions but hides this step, which is worth seeing once.
MSCK REPAIR TABLE bench_orders_csv;
