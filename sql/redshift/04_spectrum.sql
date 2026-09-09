-- Day 7 Spectrum. {redshift_role} and {glue_database} are substituted by the
-- runner.
--
-- THE SAME BYTES, THE OTHER PATH
-- ------------------------------
-- 02_load.sql read curated Parquet with COPY, straight from S3 under the
-- Redshift role's IAM policy. Lake Formation was not involved.
--
-- Spectrum reads through the catalog instead:
--
--     Redshift -> Glue Data Catalog -> Lake Formation -> S3
--
-- so the same objects are now subject to the governance Phase 6 established.
-- The role holds SpectrumCatalogRead in IAM and has no Lake Formation grant at
-- all, and IAM_ALLOWED_PRINCIPALS was removed, so the interesting question is
-- whether this path is refused - and if so, exactly where.
--
-- NOTHING HAS BEEN PRE-GRANTED. Phase 6 established that reading a governed
-- table needs two separate things: a Lake Formation grant (may this principal
-- read this table) and the IAM action lakeformation:GetDataAccess (may it ask
-- for the credentials to do so). The Redshift role has neither. Granting them
-- ahead of a failure would prove nothing; letting the failure name what is
-- missing is the evidence.
--
-- The external schema itself is metadata and should succeed either way. The
-- failure, if it comes, belongs to the query.

-- @name drop_external_schema
DROP SCHEMA IF EXISTS spectrum_lake;

-- @name create_external_schema
-- Points at the SAME Glue database the Phase 2 crawler and Phase 3 ETL use.
-- No new catalog, no copy of the metadata, no S3 change.
CREATE EXTERNAL SCHEMA spectrum_lake
FROM DATA CATALOG
DATABASE '{glue_database}'
IAM_ROLE '{redshift_role}';

-- @name list_external_tables
-- Metadata only - a catalog listing, not a data read. Expected to work even if
-- the reads below are refused, which is itself worth recording: being able to
-- see that a table exists is a different permission from being able to read it.
SELECT schemaname, tablename
FROM svv_external_tables
WHERE schemaname = 'spectrum_lake'
ORDER BY tablename;

-- @name spectrum_count_orders
-- The first actual data read through the governed path.
SELECT COUNT(*) AS orders_raw_rows
FROM spectrum_lake.orders_raw;

-- @name spectrum_vs_warehouse
-- The comparison that makes the two paths concrete. orders_raw is the
-- pre-ETL source and legitimately holds more rows than the fact: 15,861
-- against 12,060, the difference being duplicates and rejected orphans that
-- the pipeline removed. Spectrum sees the lake as it is; the warehouse sees
-- what survived validation.
SELECT
    (SELECT COUNT(*) FROM spectrum_lake.orders_raw) AS lake_raw_orders,
    (SELECT COUNT(*) FROM retail.fact_orders) AS warehouse_fact_orders;
