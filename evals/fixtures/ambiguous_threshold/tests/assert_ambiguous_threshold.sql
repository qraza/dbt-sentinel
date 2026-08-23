select * from {{ ref('daily_revenue') }} where revenue < 12000
