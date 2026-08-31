-- Phase 1 - format benchmark, table 2 of 4: JSON Lines, partitioned.
--
-- Same logical rows as the CSV table, same partition layout, different
-- encoding. JSON is the format people reach for because it is self-describing
-- and easy to emit; this table exists to put a number on what that costs.
--
-- The generator writes JSON Lines (one object per line), not a JSON array.
-- Hive's JSON SerDe requires that: an array would be one enormous record and
-- could not be split across workers at all.

DROP TABLE IF EXISTS bench_orders_json;

CREATE EXTERNAL TABLE bench_orders_json (
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
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
STORED AS TEXTFILE
LOCATION 's3://${LAKE}/benchmark/json/orders/';

MSCK REPAIR TABLE bench_orders_json;
