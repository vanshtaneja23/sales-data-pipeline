{#
  Fails with every row where `expression` is not true. Optional `where` scopes the rows checked.
  (Hand-rolled instead of dbt_utils so the project has no package download at build time.)
#}
{% test expression_is_true(model, expression, where=None) %}
select *
from {{ model }}
where not ({{ expression }})
{% if where %} and ({{ where }}) {% endif %}
{% endtest %}
