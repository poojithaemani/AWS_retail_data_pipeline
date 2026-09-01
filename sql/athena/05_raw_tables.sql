-- Phase 2 - the raw layer schema, written by hand.
--
-- These are NOT the mechanism by which the required tables are created. The
-- Glue crawler discovers the schema and scripts/publish_catalog.py republishes
-- it under the contract names. This file exists for two other reasons.
--
-- 1. AS THE EXPECTATION.
--
--    A crawler infers; it does not know what the data is supposed to be. These
--    declarations record what we believe the raw layer contains, so the
--    crawler's output can be compared against an intention rather than merely
--    inspected. Phase 2's evidence is that comparison.
--
--    That is the honest answer to "crawlers or hand-written schemas?" - it is
--    not either/or. The crawler is right about what the files contain today.
--    The hand-written definition is right about what the contract says they
--    should contain. Where they disagree, something upstream has changed, and
--    noticing that is the entire point.
--
-- 2. AS THE FALLBACK.
--
--    If the crawler is unavailable, misconfigured, or produces something
--    unusable, these statements create the same three tables directly. A
--    pipeline that can only be rebuilt by a service you do not control is not
--    reproducible.
--
-- Types below are what the data genuinely is, not what a crawler happens to
-- infer. `price` is a double because a price is a number - which is precisely
-- the claim the failure exercise puts under pressure.

-- ---------------------------------------------------------------------------
-- customers_raw
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS customers_raw;

CREATE EXTERNAL TABLE customers_raw (
    customer_id   string,
    customer_name string,
    email         string,
    country       string,
    created_date  string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
WITH SERDEPROPERTIES ('field.delim' = ',')
STORED AS TEXTFILE
LOCATION 's3://${LAKE}/raw/customers/'
TBLPROPERTIES ('skip.header.line.count' = '1');

-- created_date is declared string rather than date deliberately. The raw layer
-- holds what the source sent; parsing and validating it is Phase 3's job, and
-- typing it here would push a transformation into the catalog where it cannot
-- be tested or quarantined.

-- ---------------------------------------------------------------------------
-- products_raw
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS products_raw;

CREATE EXTERNAL TABLE products_raw (
    product_id   string,
    product_name string,
    category     string,
    price        double
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
WITH SERDEPROPERTIES ('field.delim' = ',')
STORED AS TEXTFILE
LOCATION 's3://${LAKE}/raw/products/'
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ---------------------------------------------------------------------------
-- orders_raw
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS orders_raw;

CREATE EXTERNAL TABLE orders_raw (
    order_id    string,
    customer_id string,
    product_id  string,
    quantity    int,
    order_date  string,
    status      string
)
PARTITIONED BY (
    -- Strings, not integers: the S3 directories are zero-padded (month=07),
    -- and an int partition column drops the padding and stops matching.
    year  string,
    month string,
    day   string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
WITH SERDEPROPERTIES ('field.delim' = ',')
STORED AS TEXTFILE
LOCATION 's3://${LAKE}/raw/orders/'
TBLPROPERTIES ('skip.header.line.count' = '1');

MSCK REPAIR TABLE orders_raw;
