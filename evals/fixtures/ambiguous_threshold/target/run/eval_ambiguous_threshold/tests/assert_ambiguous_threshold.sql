select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      select * from "eval"."main"."daily_revenue" where revenue < 12000
      
    ) dbt_internal_test