-- Store reference with decoded assortment and derived "since" dates.
select
    store_id,
    store_type,
    assortment                                                     as assortment_code,
    case assortment
        when 'a' then 'basic'
        when 'b' then 'extra'
        when 'c' then 'extended'
    end                                                            as assortment_level,
    competition_distance_m,
    case
        when competition_open_since_year is not null and competition_open_since_month is not null
            then make_date(competition_open_since_year, competition_open_since_month, 1)
    end                                                            as competition_open_since_date,
    has_promo2,
    case
        when has_promo2
            then to_date(promo2_since_year::text || '-' || lpad(promo2_since_week::text, 2, '0'), 'IYYY-IW')
    end                                                            as promo2_since_date,
    promo2_interval,
    _loaded_at
from {{ source('raw', 'stores') }}
