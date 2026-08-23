{{ config(materialized='table') }}
-- Ordinary-looking daily revenue. Nothing in these rows reveals whether the
-- threshold below is stale, or the business genuinely had quiet days.
select * from (values
    ('2026-08-01', 10400.00), ('2026-08-02', 9800.00), ('2026-08-03', 9100.00)
) as t(day, revenue)
