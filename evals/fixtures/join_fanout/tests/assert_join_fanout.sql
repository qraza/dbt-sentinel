select order_id from {{ ref('order_prices') }} group by order_id having count(*) > 1
