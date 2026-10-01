-- Calendar spine covering the full sales range. Grain: date_day.
with bounds as (
    select min(sales_date) as first_day, max(sales_date) as last_day
    from {{ ref('stg_sales') }}
)
select
    d::date                                  as date_day,
    extract(isoyear from d)::int             as iso_year,
    extract(week from d)::int                as iso_week,
    extract(year from d)::int                as year,
    extract(quarter from d)::int             as quarter,
    extract(month from d)::int               as month,
    extract(isodow from d)::int              as day_of_week,
    to_char(d, 'Dy')                         as day_name,
    extract(isodow from d) in (6, 7)         as is_weekend,
    date_trunc('week', d)::date              as week_start,
    date_trunc('month', d)::date             as month_start
from bounds,
     generate_series(bounds.first_day, bounds.last_day, interval '1 day') as d
