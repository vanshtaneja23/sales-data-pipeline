-- Every store in the dimension must get a tiering feature row; a store silently missing from
-- mart_store_features would silently disappear from the tiering model.
select d.store_id
from {{ ref('dim_store') }} d
left join {{ ref('mart_store_features') }} f using (store_id)
where f.store_id is null
