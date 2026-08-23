select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      select * from "eval"."main"."trips" where avg_speed_mph > 80
      
    ) dbt_internal_test