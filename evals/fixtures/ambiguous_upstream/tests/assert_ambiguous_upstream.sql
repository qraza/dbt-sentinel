select * from {{ ref('accounts') }} where status != 'ACTIVE'
