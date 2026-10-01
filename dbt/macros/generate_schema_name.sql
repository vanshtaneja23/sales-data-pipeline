{#
  Use the configured schema name verbatim (staging, marts) instead of dbt's default
  "<target_schema>_<custom_schema>" prefixing. One warehouse, one environment, readable names.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
