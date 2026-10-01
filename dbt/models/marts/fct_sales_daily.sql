{{ config(
    post_hook=[
      "create index if not exists fct_sales_daily_store_date_idx on {{ this }} (store_id, sales_date)",
      "create index if not exists fct_sales_daily_date_idx on {{ this }} (sales_date)"
    ]
) }}
-- Daily sales fact. Grain: one row per (store_id, sales_date) present in the source.
-- Missing (store, day) combinations are NOT synthesised; see the parity report for gaps.
select
    s.store_id,
    s.sales_date,
    s.day_of_week,
    s.sales_amount,
    s.customer_count,
    case when s.customer_count > 0
         then round(s.sales_amount::numeric / s.customer_count, 2)
    end                                  as sales_per_customer,
    s.is_open,
    s.is_promo,
    s.state_holiday,
    s.is_state_holiday,
    s.is_school_holiday,
    s.is_zero_sales_while_open,
    s._batch_id                          as batch_id
from {{ ref('stg_sales') }} as s
