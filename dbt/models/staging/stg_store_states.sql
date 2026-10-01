-- Store -> German federal state, with readable state names.
select
    store_id,
    state_code,
    case state_code
        when 'BE' then 'Berlin'
        when 'BW' then 'Baden-Württemberg'
        when 'BY' then 'Bavaria'
        when 'HB' then 'Bremen'
        when 'HE' then 'Hesse'
        when 'HH' then 'Hamburg'
        when 'NI' then 'Lower Saxony'
        when 'NW' then 'North Rhine-Westphalia'
        when 'RP' then 'Rhineland-Palatinate'
        when 'SH' then 'Schleswig-Holstein'
        when 'SN' then 'Saxony'
        when 'ST' then 'Saxony-Anhalt'
        when 'TH' then 'Thuringia'
        when 'HB,NI' then 'Bremen or Lower Saxony (ambiguous in source)'
    end                 as state_name,
    is_state_ambiguous,
    _loaded_at
from {{ source('raw', 'store_states') }}
