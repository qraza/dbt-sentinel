select * from {{ ref('trip_rates') }} where rate is null or not isfinite(rate)
