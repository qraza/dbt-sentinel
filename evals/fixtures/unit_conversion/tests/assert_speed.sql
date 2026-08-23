select * from {{ ref('trips') }} where avg_speed_mph > 80
