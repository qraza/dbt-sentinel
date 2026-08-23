
with raw as (
    select * from (values
        (0.81, 5), (1.05, 5), (18.23, 35), (2.50, 60), (9.24, 52)
    ) as t(trip_distance, trip_duration_minutes)
)
select
    trip_distance,
    trip_duration_minutes,
    -- PLANTED DEFECT: 600 should be 60 (minutes -> hours)
    round(600 * trip_distance / nullif(trip_duration_minutes, 0), 2) as avg_speed_mph
from raw