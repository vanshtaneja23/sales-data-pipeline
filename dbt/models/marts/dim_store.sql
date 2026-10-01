-- One row per store: reference attributes + state. Grain: store_id.
select
    s.store_id,
    s.store_type,
    s.assortment_code,
    s.assortment_level,
    s.competition_distance_m,
    case
        when s.competition_distance_m is null   then 'unknown'
        when s.competition_distance_m < 500     then '< 500 m'
        when s.competition_distance_m < 2000    then '500 m - 2 km'
        when s.competition_distance_m < 10000   then '2 - 10 km'
        else '>= 10 km'
    end                                         as competition_distance_band,
    s.competition_open_since_date,
    s.has_promo2,
    s.promo2_since_date,
    s.promo2_interval,
    st.state_code,
    st.state_name,
    st.is_state_ambiguous
from {{ ref('stg_stores') }} as s
left join {{ ref('stg_store_states') }} as st using (store_id)
