select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      select order_id from "eval"."main"."order_prices" group by order_id having count(*) > 1
      
    ) dbt_internal_test