-- Phase 5 analytics. Each statement is named by the "-- @name" line above it
-- and is run in file order by scripts/athena_analytics.py, which records the
-- bytes each one scanned. {lake_bucket} is substituted by the runner.
--
-- The set covers the brief's Day 5 Athena topics once each, against the
-- curated Parquet the Phase 3/4 pipeline produced. It is evidence, not a
-- framework: the point is that the lake answers business questions, and what
-- each answer costs to read.
--
-- The DDL comes first because curated/ and quarantine/ are not in the catalog.
-- The crawler deliberately targets raw/ only - both output folders are named
-- `sales`, so crawling them would collide on one table name, and Phase 2's
-- publish audit would see tables it does not expect. Declaring them here keeps
-- that contract intact and keeps the definitions visible.

-- @name ddl_curated_sales
CREATE EXTERNAL TABLE IF NOT EXISTS curated_sales (
    order_id     string,
    customer_id  string,
    product_id   string,
    product_name string,
    category     string,
    country      string,
    quantity     int,
    price        double,
    order_total  decimal(12,2),
    status       string,
    order_date   timestamp
)
PARTITIONED BY (year string, month string, day string)
STORED AS PARQUET
LOCATION 's3://{lake_bucket}/curated/sales/';

-- @name ddl_curated_partitions
-- The partitions exist in S3 but not in the catalog until they are registered.
MSCK REPAIR TABLE curated_sales;

-- @name ddl_quarantine_sales
-- Unpartitioned, because a row rejected *for* a malformed date has no date to
-- partition on.
CREATE EXTERNAL TABLE IF NOT EXISTS quarantine_sales (
    order_id         string,
    customer_id      string,
    product_id       string,
    quantity         int,
    order_date       timestamp,
    status           string,
    rejection_reason string
)
STORED AS PARQUET
LOCATION 's3://{lake_bucket}/quarantine/sales/';

-- @name pruned_single_day
-- Partition pruning: restricting on the partition columns should read one
-- partition. Compare its bytes scanned with full_scan_same_answer below.
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM curated_sales
WHERE year = '2026' AND month = '08' AND day = '19';

-- @name full_scan_same_answer
-- The same question asked so the engine cannot prune - the filter is on a
-- derived value rather than the partition columns. Identical answer, far more
-- data read. This is what "cost implications of scanning unnecessary data"
-- means in practice.
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM curated_sales
WHERE date_format(order_date, '%Y-%m-%d') = '2026-08-19';

-- @name single_column_projection
-- Parquet is columnar: reading one column should scan a fraction of what
-- touching many does, over exactly the same rows.
SELECT SUM(order_total) AS revenue FROM curated_sales;

-- @name many_column_projection
-- The comparison for single_column_projection - same rows, several columns.
SELECT COUNT(*) AS rows_seen, MIN(order_id) AS lo, MAX(status) AS hi,
       MIN(product_name) AS a_product, MAX(country) AS a_country
FROM curated_sales;

-- @name revenue_by_category
-- Aggregation and grouping.
SELECT category,
       COUNT(*)                   AS orders,
       SUM(order_total)           AS revenue,
       ROUND(AVG(order_total), 2) AS avg_order
FROM curated_sales
GROUP BY category
ORDER BY revenue DESC;

-- @name top_customers_by_country
-- Window function: RANK() over a partition, ordered - rank customers inside
-- their own country, which a plain GROUP BY cannot express. One window
-- statement, not two: a running total would exercise the same feature and
-- add a second full scan for no extra evidence.
SELECT country, customer_id, revenue, rnk
FROM (
    SELECT country,
           customer_id,
           SUM(order_total) AS revenue,
           RANK() OVER (PARTITION BY country ORDER BY SUM(order_total) DESC) AS rnk
    FROM curated_sales
    GROUP BY country, customer_id
)
WHERE rnk <= 3
ORDER BY country, rnk;

-- @name join_curated_to_raw_customers
-- A join across the curated fact and a raw dimension - Parquet against CSV in
-- one query, resolved through the Glue Catalog.
SELECT c.country,
       COUNT(DISTINCT s.customer_id) AS buyers,
       SUM(s.order_total)            AS revenue
FROM curated_sales s
JOIN customers_raw c ON s.customer_id = c.customer_id
GROUP BY c.country
ORDER BY revenue DESC;

-- @name quarantine_by_reason
-- The quality question asked of the quarantine dataset the pipeline writes.
-- This is "the pipeline should not blindly publish invalid data" made
-- queryable rather than merely logged.
SELECT rejection_reason, COUNT(*) AS rows_rejected
FROM quarantine_sales
GROUP BY rejection_reason
ORDER BY rows_rejected DESC;

-- @name ctas_drop_if_exists
DROP TABLE IF EXISTS sales_by_category_ctas;

-- @name ctas_sales_by_category
-- CTAS: materialise an aggregate as its own Parquet table. The point is that
-- the expensive scan happens once and every later read of the summary is
-- cheap - the same trade the curated layer makes, one level up.
--
-- No external_location, deliberately. The first attempt set one and Athena
-- refused it:
--
--     The Create Table As Select query failed because it was submitted with an
--     'external_location' property to an Athena Workgroup that enforces a
--     centralized output location for all queries.
--
-- That is the Phase 0 workgroup doing its job - enforce_workgroup_configuration
-- exists precisely so no query can choose its own output path and escape the
-- KMS-encrypted, cost-capped result location. The governance control and the
-- convenience property are mutually exclusive, and the control wins.
CREATE TABLE sales_by_category_ctas
WITH (
    format = 'PARQUET',
    write_compression = 'SNAPPY'
) AS
SELECT category,
       country,
       COUNT(*)         AS orders,
       SUM(order_total) AS revenue
FROM curated_sales
GROUP BY category, country;

-- @name ctas_read_back
-- Reading the materialised summary. Compare its bytes scanned against
-- revenue_by_category, which computes the same shape from the full fact table.
SELECT category, SUM(revenue) AS revenue
FROM sales_by_category_ctas
GROUP BY category
ORDER BY revenue DESC;
