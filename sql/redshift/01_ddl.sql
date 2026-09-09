-- Day 7 star schema. Statements are named by the "-- @name" line above them and
-- run in file order by scripts/run_redshift.py through the Redshift Data API.
--
--             dim_customer
--                  |
--   dim_product - fact_orders - dim_date
--
-- WHY THIS DIFFERS FROM THE LAKE, WHICH IS THE DAY 7 QUESTION
-- ----------------------------------------------------------
-- curated_sales is already joined and denormalised: one wide row per order
-- carrying product_name, category and country inline. That is right for a lake.
-- A file that explains itself needs no join, and anything that can read Parquet
-- can use it.
--
-- A warehouse wants the opposite trade. Splitting descriptive attributes into
-- dimensions means the same country string is stored once rather than twelve
-- thousand times, a filter on category never scans the fact, and distribution
-- and sort keys can be chosen per table for how each is actually queried. The
-- star is not a tidier lake - it buys join work at query time in exchange for
-- scan and storage efficiency, and that is only worth it because analytical
-- queries filter and aggregate far more than they select whole rows.
--
-- Idempotent by construction: the schema is dropped and recreated, so this file
-- can be re-run at any point without leaving half a model behind.

-- @name drop_schema
DROP SCHEMA IF EXISTS retail CASCADE;

-- @name create_schema
CREATE SCHEMA retail;

-- @name dim_customer
-- DISTSTYLE ALL: a thousand rows on every node costs almost nothing and takes
-- this table out of every join's shuffle. Small, slow-changing dimensions
-- belong everywhere; only the fact is worth distributing.
CREATE TABLE retail.dim_customer (
    customer_id VARCHAR(16) NOT NULL ENCODE ZSTD,
    country     VARCHAR(8) ENCODE BYTEDICT,
    PRIMARY KEY (customer_id)
)
DISTSTYLE ALL
SORTKEY (customer_id);

-- @name dim_product
CREATE TABLE retail.dim_product (
    product_id   VARCHAR(16) NOT NULL ENCODE ZSTD,
    product_name VARCHAR(128) ENCODE ZSTD,
    category     VARCHAR(64) ENCODE BYTEDICT,
    price        DECIMAL(12,2),
    PRIMARY KEY (product_id)
)
DISTSTYLE ALL
SORTKEY (product_id);

-- @name dim_date
-- The one dimension with no source in the lake. Conformed date dimensions are
-- generated rather than ingested: the calendar is not business data, and
-- waiting for a source system to send it would be odd.
CREATE TABLE retail.dim_date (
    date_key   DATE NOT NULL,
    year       SMALLINT,
    month      SMALLINT,
    day        SMALLINT,
    day_name   VARCHAR(9),
    is_weekend BOOLEAN,
    PRIMARY KEY (date_key)
)
DISTSTYLE ALL
SORTKEY (date_key);

-- @name fact_orders
-- The only table where distribution actually matters.
--
-- DISTKEY (customer_id): the brief's own example query joins to dim_customer,
-- and distributing the fact on that key keeps the join node-local. dim_customer
-- is DISTSTYLE ALL so this is belt and braces today - but the key is chosen for
-- how the table is queried, not for how it happens to load, and it is what
-- would still be right if the dimension outgrew ALL.
--
-- SORTKEY (order_date): every analytical question here is bounded by time.
-- Redshift's zone maps then skip blocks whose min/max cannot match a dated
-- predicate. That is the same idea as Athena's partition pruning in Phase 5,
-- and 03_analytics.sql measures it rather than asserting it.
CREATE TABLE retail.fact_orders (
    order_id    VARCHAR(16) NOT NULL ENCODE ZSTD,
    customer_id VARCHAR(16) ENCODE ZSTD,
    product_id  VARCHAR(16) ENCODE ZSTD,
    order_date  TIMESTAMP,
    quantity    INTEGER,
    price       DECIMAL(12,2),
    order_total DECIMAL(12,2),
    status      VARCHAR(16) ENCODE BYTEDICT,
    PRIMARY KEY (order_id)
)
DISTSTYLE KEY
DISTKEY (customer_id)
SORTKEY (order_date);

-- @name confirm_tables
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'retail'
ORDER BY table_name;
