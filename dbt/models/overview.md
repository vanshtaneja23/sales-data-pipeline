{% docs __overview__ %}
# Rossmann sales warehouse

Batch pipeline orchestrated by Airflow (DAG `sales_pipeline`). Three public sources are
downloaded, checksum-verified, cleaned with pandas against YAML data contracts and loaded into
the `raw` schema. dbt then builds:

- **staging** (views): `stg_sales`, `stg_stores`, `stg_store_states` — renamed, decoded, no filtering.
- **marts** (tables): `fct_sales_daily` (store x day fact), `dim_store`, `dim_date`, and
  `mart_store_features` (per-store inputs for the tiering model).

Lineage: `raw.sales -> stg_sales -> fct_sales_daily -> mart_store_features`;
`raw.stores + raw.store_states -> stg_* -> dim_store`.

Operational tables live in schema `ops`: `ops.load_audit` (one row per source per load with
row counts and cleaning issues), `ops.dq_results` (every quality check outcome) and
`ops.parity_report` (cross-source discrepancies).
{% enddocs %}
