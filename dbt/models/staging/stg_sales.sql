-- One row per store per calendar day, business-friendly names, no filtering.
select
    store_id,
    sales_date,
    day_of_week,
    sales_amount,
    customer_count,
    is_open,
    is_promo,
    state_holiday,
    state_holiday <> 'none'           as is_state_holiday,
    is_school_holiday,
    is_zero_sales_while_open,
    _batch_id,
    _loaded_at
from {{ source('raw', 'sales') }}
