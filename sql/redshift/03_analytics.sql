-- Day 7 analytics. Fact/dimension joins, aggregation, date analysis, product
-- analysis, and the query-optimisation evidence.
--
-- ON MEASURING RATHER THAN ASSERTING
-- ----------------------------------
-- The sort-key exercise below is a pair of queries that return the SAME answer
-- by different routes, so the difference between them is attributable. The
-- runner records each statement's duration, and the EXPLAIN statements capture
-- what the planner intended.
--
-- At twelve thousand rows both queries are fast in absolute terms, and any
-- honest report has to say so: the interesting evidence is the plan and the
-- work avoided, not a stopwatch reading on a dataset this small. Timings at
-- this scale are dominated by fixed overhead and should not be quoted as a
-- speedup.

-- @name revenue_by_country
-- The brief's Day 7 example query (p.16), verbatim in intent: the fact joined
-- to a dimension, aggregated by an attribute the fact no longer carries.
SELECT c.country,
       SUM(f.order_total) AS revenue
FROM retail.fact_orders f
JOIN retail.dim_customer c ON f.customer_id = c.customer_id
GROUP BY c.country
ORDER BY revenue DESC;

-- @name revenue_by_category
-- Product/category analysis - the other dimension, same shape of question.
SELECT p.category,
       COUNT(*) AS orders,
       SUM(f.order_total) AS revenue,
       ROUND(AVG(f.order_total), 2) AS avg_order
FROM retail.fact_orders f
JOIN retail.dim_product p ON f.product_id = p.product_id
GROUP BY p.category
ORDER BY revenue DESC;

-- @name monthly_revenue_trend
-- Date-based analysis through dim_date rather than by parsing the timestamp,
-- which is the reason a date dimension exists at all: calendar attributes
-- become joinable columns instead of expressions on every query.
SELECT d.year,
       d.month,
       COUNT(*) AS orders,
       SUM(f.order_total) AS revenue
FROM retail.fact_orders f
JOIN retail.dim_date d ON DATE(f.order_date) = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;

-- @name star_join_all_dimensions
-- The whole star at once - the shape a BI tool actually emits.
SELECT p.category,
       c.country,
       d.year,
       d.month,
       COUNT(*) AS orders,
       SUM(f.order_total) AS revenue
FROM retail.fact_orders f
JOIN retail.dim_product p ON f.product_id = p.product_id
JOIN retail.dim_customer c ON f.customer_id = c.customer_id
JOIN retail.dim_date d ON DATE(f.order_date) = d.date_key
GROUP BY p.category, c.country, d.year, d.month
ORDER BY revenue DESC
LIMIT 20;

-- @name explain_pruned
-- The plan for a predicate the sort key can serve.
EXPLAIN
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM retail.fact_orders
WHERE order_date >= '2026-08-19' AND order_date < '2026-08-20';

-- @name pruned_by_sortkey
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM retail.fact_orders
WHERE order_date >= '2026-08-19' AND order_date < '2026-08-20';

-- @name explain_unpruned
-- The plan for the identical question asked so the sort key cannot help: the
-- predicate is wrapped in a function, so the column is no longer directly
-- comparable and zone maps cannot eliminate blocks.
EXPLAIN
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM retail.fact_orders
WHERE TO_CHAR(order_date, 'YYYY-MM-DD') = '2026-08-19';

-- @name unpruned_same_answer
SELECT COUNT(*) AS orders, SUM(order_total) AS revenue
FROM retail.fact_orders
WHERE TO_CHAR(order_date, 'YYYY-MM-DD') = '2026-08-19';

-- @name table_design_check
-- What Redshift actually recorded for the distribution and sort choices, and
-- how skewed the fact ended up across slices.
SELECT "table", diststyle, sortkey1, size, tbl_rows, skew_rows
FROM svv_table_info
WHERE schema = 'retail'
ORDER BY "table";
