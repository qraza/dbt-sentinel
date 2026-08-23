
select * from (values
    (1, 'GB', 42.00), (2, 'GB', 38.50), (3, 'FR', 51.20),
    (4, 'FR', 47.90), (5, 'DE', 44.10)
) as t(order_id, country, order_total)