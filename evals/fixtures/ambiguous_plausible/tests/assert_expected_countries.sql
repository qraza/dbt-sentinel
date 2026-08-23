-- Fails because FR/DE are not in the allowed set. The sampled rows themselves look
-- entirely normal: nothing in them explains WHY the allow-list is wrong, or whether
-- the data or the test is at fault.
select * from {{ ref('orders') }} where country not in ('GB')
