{{ config(materialized='table') }}
-- The offending values arrive from upstream; nothing here explains why.
select * from (values (1,'ACTIVE'),(2,'active'),(3,'Active')) as t(id, status)
