select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      select * from "eval"."main"."trip_rates" where rate is null or not isfinite(rate)
      
    ) dbt_internal_test