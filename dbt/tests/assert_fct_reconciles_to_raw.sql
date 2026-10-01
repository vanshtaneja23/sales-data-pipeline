-- Source-to-target reconciliation: per month, the fact table must carry exactly the rows and
-- revenue that were loaded into raw.sales. Any transformation that drops, duplicates or alters
-- rows shows up here as a mismatching month.
with raw_m as (
    select date_trunc('month', sales_date)::date as month, count(*) as row_count, sum(sales_amount) as revenue
    from {{ source('raw', 'sales') }}
    group by 1
),
fct_m as (
    select date_trunc('month', sales_date)::date as month, count(*) as row_count, sum(sales_amount) as revenue
    from {{ ref('fct_sales_daily') }}
    group by 1
)
select
    coalesce(r.month, f.month) as month,
    r.row_count as raw_rows, f.row_count as fct_rows,
    r.revenue   as raw_revenue, f.revenue as fct_revenue
from raw_m r
full outer join fct_m f using (month)
where r.row_count is distinct from f.row_count
   or r.revenue   is distinct from f.revenue
