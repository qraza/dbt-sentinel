select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      select * from "eval"."main"."accounts" where status != 'ACTIVE'
      
    ) dbt_internal_test