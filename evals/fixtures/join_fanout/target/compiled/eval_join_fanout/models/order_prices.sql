
with orders as (
    select * from (values (1,'a'),(2,'b'),(3,'c')) as t(order_id, sku)
),
prices as (
    -- PLANTED DEFECT: sku 'a' appears twice, so the join duplicates order 1
    select * from (values ('a',10.0),('a',12.0),('b',20.0),('c',30.0)) as t(sku, price)
)
select o.order_id, o.sku, p.price
from orders o join prices p on o.sku = p.sku