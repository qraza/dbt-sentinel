{{ config(materialized='table') }}
with trips as (
    select * from (values (1,10.0,5),(2,8.0,0),(3,6.0,3)) as t(id, distance, minutes)
)
-- PLANTED DEFECT: no nullif guard, so minutes = 0 yields a null/infinite rate
select id, distance, minutes, distance / minutes as rate
from trips
