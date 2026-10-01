# Data dictionary: Rossmann sales warehouse

This is the business-facing companion to the dbt docs (`dbt docs generate`). The dbt docs describe
every model and column; this page covers what dbt doesn't: where the data comes from, what the
operational tables mean, which quality checks run and what happens when they fail, and the
definitions analysts keep asking about.

## Schemas

| Schema | Built by | Contents |
|---|---|---|
| `raw` | Airflow `load_<source>` tasks | Typed, cleaned copies of the three source files. Fully replaced every run. |
| `staging` | dbt (views) | `stg_sales`, `stg_stores`, `stg_store_states`: renamed and decoded, no rows filtered. |
| `marts` | dbt (tables) | `fct_sales_daily`, `dim_store`, `dim_date`, `mart_store_features`. Use these for analysis. |
| `ops` | pipeline code | `load_audit`, `dq_results`, `parity_report`, `pipeline_alerts`. Operational metadata. |
| `rag` | `make rag-index` | `chunks`: embedded documentation for the Q&A assistant; `answer_cache`. |

## Sources and provenance

| Source | Raw table | Rows | Origin |
|---|---|---|---|
| sales | `raw.sales` | 1,017,209 | Kaggle "Rossmann Store Sales" `train.csv` (the mirror stores it as a zip archive despite the .csv name) |
| stores | `raw.stores` | 1,115 | Kaggle `store.csv` |
| store_states | `raw.store_states` | 1,115 | Community `store_states.csv` from the 3rd-place Kaggle solution (entron/entity-embedding-rossmann) |

Every source is pinned to a specific git commit **and** a SHA-256 checksum in
`src/sales_pipeline/sources.py`. If the downloaded bytes don't match the checksum, the extract task
fails with `SourceIntegrityError`; nothing is loaded. Changing a pin is a deliberate code change.

The data covers **2013-01-01 to 2015-07-31** (942 calendar days) for **1,115 stores**. It is a frozen
historical extract, not a live feed.

## Glossary

**Trading day.** A store-day where `is_open` is true *and* `is_zero_sales_while_open` is false. All
"average per day" metrics in `mart_store_features` are per trading day, so closed days and
data glitches don't drag averages down.

**Zero sales while open.** 54 store-days are marked open but report €0 sales. They are kept in the
fact table, flagged with `is_zero_sales_while_open`, counted in `ops.load_audit`, and excluded from
averages and from the basket-size test. Two of them still report customers (e.g. store 948 on
2013-04-25 with 5 customers).

**Like-for-like growth window.** `sales_growth_yoy` compares 2015-01-01..2015-06-30 with
2014-01-01..2014-06-30. Full-year growth isn't possible because 180 stores have no data for
H2-2014. The window dates are dbt vars (`growth_base_start` etc.) in `dbt_project.yml`.

**H2-2014 gap.** 180 stores have no rows from 2014-07-01 to 2014-12-31 in the upstream file, and store
988 is missing 2013-01-01. Together that's 33,121 missing store-days. They are not zero-filled.
The parity check reports them as status `known` under the allowlist rules
`KAGGLE-H2-2014-GAP` and `STORE-988-2013-01-01` in `contracts/parity.yml`.

**Batch id.** The Airflow `run_id` of the run that loaded a row (`_batch_id` in raw tables,
`batch_id` in `fct_sales_daily`). It links any row back to its load audit and quality results.

**as_of_date.** A DAG parameter (default `2015-07-31`, the last day in the data). The freshness
check measures how far the latest sales date lags behind it. In a live pipeline it would be the run's
logical date; it is a parameter here because the source is historical.

**Store tier.** A, B, C or D from the weighted store-tiering model: top 20%, next 30%, next 30%,
bottom 20% of scores. See `analytics/TIERING.md`.

**Borderline store.** A store that keeps its tier in fewer than 80% of 1,000 re-weighted
scenarios (`is_borderline` in `analytics/store_tiers.csv`); 357 stores in the current run.

**Store type.** Codes a, b, c, d for the store model. Rossmann does not publish what they mean.
Type b is rare (17 stores).

**Assortment.** a = basic, b = extra, c = extended.

**Promo vs Promo2.** `is_promo` is a short-term promotion on a specific day (Monday–Friday only).
`has_promo2` is a continuing, recurring promotion a store opted into; `promo2_interval` lists the
months each new round starts.

## Operational tables (`ops` schema)

### ops.load_audit
One row per source per load (primary key `batch_id, source`).

| Column | Meaning |
|---|---|
| batch_id | Airflow run id |
| source | sales, stores or store_states |
| source_sha256 | checksum of the file that was loaded |
| rows_in | rows read from the file |
| rows_loaded | rows in the table after COPY (must equal the cleaned row count or the load aborts) |
| issues | JSON counts of known quirks handled during cleaning, e.g. `{"zero_sales_while_open": 54}`, `{"promo_interval_sept_normalised": 106}`, `{"ambiguous_state": 22}` |
| loaded_at | load timestamp |

### ops.dq_results
One row per data-quality check per run. `status` is `pass`, `warn` or `fail`. Columns:
`batch_id, check_name, source, status, observed, expected, detail, checked_at`. Results are
committed **before** a failing check raises, so a failed run always leaves a record of why.

### ops.parity_report
One row per parity check per run: `check_name, left_source, right_source, status
(pass | known | fail), discrepancies (count), sample (JSON examples), detail`.

### ops.pipeline_alerts
One row per failed task, written by the DAG's `on_failure_callback`: `dag_id, task_id, run_id,
error, created_at`. The same alert is also logged as a structured `PIPELINE_ALERT` JSON line.

## Data-quality checks

| Check | Where | Fails the run when |
|---|---|---|
| Source checksum | `extract_<source>` | downloaded bytes don't match the pinned SHA-256 |
| Schema drift | `validate_<source>` (before load) | a column was added, removed or renamed vs the contract (reordering is allowed) |
| Type drift | `validate_<source>` | any value can't be cast to its contract type, code list or range; all bad columns are reported at once |
| Row volume bounds | `validate_<source>` | row count is outside the contract's `[min_rows, max_rows]` (sales: 1,000,000–1,100,000) |
| Row volume change | `validate_<source>` | row count moved more than `max_change_pct` vs the last load (sales 5%, stores/states 2%); the first ever load only warns |
| Freshness | `check_freshness` (after load) | latest `sales_date` lags `as_of_date` by more than 3 days, or is in the future |
| Load freshness | `dbt_source_freshness` | `_loaded_at` older than 48 hours (warn after 24) |
| Parity | `parity_check` | stores missing from either reference file, sales for unknown stores, or missing store-days not covered by an allowlist rule (or a known gap affecting more stores than baselined) |
| dbt tests | `dbt_build` | any of 33 data tests fails: unique/not-null keys, relationships, accepted values, `(store_id, sales_date)` grain, row-count bounds, closed days having zero sales and customers, basket size between €0.50 and €200, growth between −90% and +300%, raw-to-fact monthly reconciliation, every store having a features row |

**Failure behaviour.** Quality tasks never retry (a data problem doesn't fix itself in 30 seconds).
A failing check fails its task, every downstream task is skipped (`upstream_failed`), so the marts
keep serving the last good build, and the failure callback writes to `ops.pipeline_alerts`. Only
`extract_*` tasks retry (twice, 30 s apart) because downloads can fail transiently.

## Running and operating

* `make up`: start Postgres + Airflow (UI on http://localhost:8080).
* `make run`: run the whole DAG once in-process and stream logs.
* `make drill-stale`: failure drill; triggers a run with `as_of_date = 2015-09-30` (61 days after the
  data ends), which must fail at `check_freshness`.
* **Adding a known issue:** add a rule with an `id`, date range, optional `stores`, `max_stores` and a
  `reason` to `contracts/parity.yml`. Never widen a rule silently; the reason is the audit trail.
* **Upstream schema change:** the run fails at schema drift. Update `contracts/<source>.yml`
  deliberately; if the table shape changes, the load fails with `WarehouseSchemaError` until the
  table is migrated.
