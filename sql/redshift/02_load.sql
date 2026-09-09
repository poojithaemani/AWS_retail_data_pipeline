-- Day 7 load. {lake_bucket} and {redshift_role} are substituted by the runner.
--
-- COPY IS A DIRECT S3 PATH, DELIBERATELY
-- --------------------------------------
-- It reads the curated Parquet under the Redshift role's own IAM policy -
-- ListCuratedPrefix, ReadCuratedObjects and DecryptCuratedObjects, all scoped
-- to curated/* and the lake key in the persistent layer. Lake Formation is not
-- consulted at any point: no grant, no credential vending, no catalog lookup.
--
-- 04_spectrum.sql reads the same bytes through the governed path instead. The
-- contrast between the two is the point of the exercise, and it is only visible
-- because Phase 6 removed IAM_ALLOWED_PRINCIPALS - before that, both paths
-- would have worked for uninteresting reasons.
--
-- Re-runnable via 01_ddl.sql, which drops and recreates the schema. This file
-- does NOT truncate first: running it twice against a loaded schema would
-- double every count. Explicit truncates were written and then removed - they
-- guarded a way of running the stages that nothing actually does, and an
-- idempotency claim nobody tests is worth less than an honest note.

-- @name drop_stg_if_exists
DROP TABLE IF EXISTS retail.stg_sales;

-- @name stg_sales
-- A landing table shaped like the curated row, which is wide: product_name,
-- category and country all travel inline with each order.
--
-- The star cannot be loaded from it directly, and that gap IS the
-- normalisation step this phase is about. COPY lands the wide row here, the
-- dimensions and the narrow fact are derived from it, then it is dropped. It
-- exists for the duration of a load and no longer.
-- The column TYPES here mirror the Parquet exactly, not the warehouse model.
-- COPY from a columnar format matches by POSITION and will not silently
-- convert, so staging has to describe the file rather than the destination:
--
--     order_id..country  string          -> VARCHAR
--     quantity           int32           -> INTEGER
--     price              double          -> DOUBLE PRECISION   <-- not DECIMAL
--     order_total        decimal128(12,2)-> DECIMAL(12,2)
--     order_date         timestamp[ns]   -> TIMESTAMP
--
-- price is the one that bites. It is a double in the lake because products.price
-- was inferred as a double back in Phase 2, while order_total was computed as an
-- explicit decimal in Phase 3 to keep money exact. Declaring price as DECIMAL
-- here failed the COPY with
--
--     Spectrum Scan Error, code 15007
--
-- naming a file rather than a column, which is a genuinely unhelpful message
-- for a type mismatch. It is cast to DECIMAL(12,2) on the way into the star,
-- so the warehouse model is unaffected - only staging tolerates the double.
CREATE TABLE retail.stg_sales (
    order_id     VARCHAR(16),
    customer_id  VARCHAR(16),
    product_id   VARCHAR(16),
    product_name VARCHAR(128),
    category     VARCHAR(64),
    country      VARCHAR(8),
    quantity     INTEGER,
    price        DOUBLE PRECISION,
    order_total  DECIMAL(12,2),
    status       VARCHAR(16),
    order_date   TIMESTAMP
)
DISTSTYLE EVEN;

-- @name copy_stg_sales
-- year/month/day are absent by design: Spark wrote them as partition
-- directories, so they are path segments rather than columns inside the files.
COPY retail.stg_sales
FROM 's3://{lake_bucket}/curated/sales/'
IAM_ROLE '{redshift_role}'
FORMAT AS PARQUET;

-- @name load_dim_customer
-- Dimensions are derived from the staged row rather than reloaded from the
-- lake. Splitting the descriptive columns out here is the denormalised-to-star
-- step, done in SQL so the lake itself is never touched.
INSERT INTO retail.dim_customer (customer_id, country)
SELECT customer_id, MAX(country)
FROM retail.stg_sales
GROUP BY customer_id;

-- @name load_dim_product
INSERT INTO retail.dim_product (product_id, product_name, category, price)
SELECT product_id, MAX(product_name), MAX(category),
       CAST(MAX(price) AS DECIMAL(12,2))
FROM retail.stg_sales
GROUP BY product_id;

-- @name load_fact_orders
-- The fact keeps measures and foreign keys only. Every descriptive attribute
-- now lives in a dimension, which is what makes the fact narrow enough for the
-- sort key to be worth having.
INSERT INTO retail.fact_orders (
    order_id, customer_id, product_id, order_date, quantity, price, order_total, status
)
SELECT order_id, customer_id, product_id, order_date, quantity,
       CAST(price AS DECIMAL(12,2)), order_total, status
FROM retail.stg_sales;

-- @name load_dim_date
-- Generated from the fact's own range. No new S3 source, no generator change.
INSERT INTO retail.dim_date (date_key, year, month, day, day_name, is_weekend)
SELECT DISTINCT
       DATE(order_date),
       EXTRACT(year FROM order_date),
       EXTRACT(month FROM order_date),
       EXTRACT(day FROM order_date),
       TO_CHAR(order_date, 'Day'),
       EXTRACT(dow FROM order_date) IN (0, 6)
FROM retail.fact_orders;

-- @name drop_stg_sales
DROP TABLE retail.stg_sales;

-- @name analyze_tables
-- Statistics drive the planner's join and scan choices. Without them the
-- EXPLAIN output in 03_analytics.sql would describe guesses rather than the
-- plan the data actually justifies.
ANALYZE retail.fact_orders;

-- @name reconcile_fact
-- THE load assertion. Must be exactly 12,060 rows and 10705326.72 - the
-- figures the lake was independently validated at. A warehouse is a copy, so
-- the only interesting question about the load is whether it is faithful; a
-- star schema that quietly loses or duplicates rows during normalisation is
-- worse than none, because the numbers still look plausible.
SELECT COUNT(*) AS fact_rows,
       COUNT(DISTINCT order_id) AS distinct_orders,
       SUM(order_total) AS revenue
FROM retail.fact_orders;

-- @name reconcile_dims
SELECT (SELECT COUNT(*) FROM retail.dim_customer) AS customers,
       (SELECT COUNT(*) FROM retail.dim_product) AS products,
       (SELECT COUNT(*) FROM retail.dim_date) AS dates;

