-- Per-store performance features that feed the store-tiering model. Grain: store_id.
-- SQL computes the facts; the weighting/tiering judgement lives in Python (analytics/).
with daily as (
    select * from {{ ref('fct_sales_daily') }}
),

-- "Trading days": open and actually trading. The 54 open-but-zero-sales rows are excluded
-- so a data glitch can't drag down a store's average.
trading as (
    select * from daily where is_open and not is_zero_sales_while_open
),

base as (
    select
        store_id,
        count(*)                                                    as trading_days,
        sum(sales_amount)                                           as total_sales,
        round(avg(sales_amount), 2)                                 as avg_daily_sales,
        round(avg(customer_count), 2)                               as avg_daily_customers,
        round(sum(sales_amount)::numeric / nullif(sum(customer_count), 0), 4) as avg_sales_per_customer
    from trading
    group by store_id
),

-- Same-window YoY growth. H2 2014 is missing for 180 stores, so we compare H1 2015 to H1 2014,
-- the latest like-for-like window that every store has.
growth as (
    select
        store_id,
        sum(sales_amount) filter (
            where sales_date between '{{ var("growth_base_start") }}' and '{{ var("growth_base_end") }}'
        )                                                           as sales_growth_base,
        sum(sales_amount) filter (
            where sales_date between '{{ var("growth_curr_start") }}' and '{{ var("growth_curr_end") }}'
        )                                                           as sales_growth_current
    from daily
    group by store_id
),

-- Stability: coefficient of variation of weekly sales, over complete weeks only
-- (7 rows present) so partial weeks at the edges or around data gaps don't inflate it.
weekly as (
    select store_id, date_trunc('week', sales_date)::date as week_start,
           sum(sales_amount) as week_sales, count(*) as days_in_week
    from daily
    group by 1, 2
),

stability as (
    select
        store_id,
        count(*)                                                    as complete_weeks,
        round(stddev_samp(week_sales) / nullif(avg(week_sales), 0), 4) as weekly_sales_cv
    from weekly
    where days_in_week = 7
    group by store_id
),

-- Promo lift on comparable days: Mon-Fri (the only days promos run), non-holiday, trading.
promo as (
    select
        store_id,
        avg(sales_amount) filter (where is_promo)                   as avg_sales_promo_day,
        avg(sales_amount) filter (where not is_promo)               as avg_sales_non_promo_day
    from trading
    where day_of_week between 1 and 5 and not is_state_holiday
    group by store_id
),

coverage as (
    select store_id, count(*) as days_present
    from daily
    group by store_id
)

select
    b.store_id,
    b.trading_days,
    b.total_sales,
    b.avg_daily_sales,
    b.avg_daily_customers,
    b.avg_sales_per_customer,
    g.sales_growth_base,
    g.sales_growth_current,
    round(g.sales_growth_current::numeric / nullif(g.sales_growth_base, 0) - 1, 4) as sales_growth_yoy,
    s.complete_weeks,
    s.weekly_sales_cv,
    round(p.avg_sales_promo_day / nullif(p.avg_sales_non_promo_day, 0) - 1, 4)     as promo_lift,
    (select count(*) from {{ ref('dim_date') }}) - c.days_present                  as days_missing
from base as b
left join growth    as g using (store_id)
left join stability as s using (store_id)
left join promo     as p using (store_id)
left join coverage  as c using (store_id)
