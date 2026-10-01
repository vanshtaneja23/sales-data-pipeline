# RAG evaluation report

Model: `local:Qwen/Qwen2.5-3B-Instruct` · embeddings: `BAAI/bge-small-en-v1.5` · corpus `a23501b497662826` · 49 questions (41 answerable, 8 out-of-scope) · retrieval `vector` · fallback threshold 0.625

## Retrieval (answerable questions)

| mode | hit@1 | hit@3 | hit@5 | MRR@5 |
|---|---|---|---|---|
| vector | 0.805 | 0.976 | 0.976 | 0.878 |
| hybrid | 0.732 | 0.976 | 0.976 | 0.837 |

## Answers

* Answer accuracy (deterministic key-fact check): overall **0.816**, answerable 0.78, out-of-scope correctly declined 1.0
* Fallback (low retrieval confidence) rate: answerable 0.0, out-of-scope 0.875
* LLM-as-judge verdicts: {'CORRECT': 26, 'PARTIAL': 19, 'INCORRECT': 4, 'UNPARSEABLE': 0, 'SKIPPED': 0}; judge agrees with fact check on 0.633 of judged answers
* Confidently wrong (answered, judged INCORRECT): ['q27', 'q32'] (0.056 of answered)
* Answered but failed the key-fact check: ['q19', 'q27', 'q34', 'q38']
* False abstentions (answerable, but the system said it doesn't know): ['q02', 'q21', 'q22', 'q24', 'q33']
* Latency: p50 2898.6 ms, p95 25780.6 ms (generated); cache miss p50 2583.9 ms vs hit 1.6 ms

## Per question

| id | answerable | rank | fallback | facts ok | judge | answer |
|---|---|---|---|---|---|---|
| q01 | True | 1 | False | True | CORRECT | The grain of the fct_sales_daily table is one row per store per calendar day [1]. |
| q02 | True | 1 | False | False | CORRECT | I don't know based on the pipeline documentation. [1] |
| q03 | True | 2 | False | True | CORRECT | The sales data covers the period from 2013-01-01 to 2015-07-31. This date range is derived from the source table `raw.sales` which contains daily sales records  |
| q04 | True | 1 | False | True | PARTIAL | Sales_per_customer is calculated as sales_amount divided by customer_count, rounded to 2 decimals. On days with zero customers, sales_per_customer is NULL, as i |
| q05 | True | 1 | False | True | PARTIAL | Assortment code 'c' means extended [1]. |
| q06 | True | 1 | False | True | PARTIAL | Based on the information provided in [1] and [2], the store_type codes a, b, c, and d have no published meaning by Rossmann. They are used to categorize differe |
| q07 | True | 1 | False | True | PARTIAL | Sales_growth_yoy compares the sales in the current growth window, 2015-01-01..2015-06-30, with the sales in the base growth window, 2014-01-01..2014-06-30. [2][ |
| q08 | True | 1 | False | True | PARTIAL | Year-over-year growth is measured first half versus first half instead of full year because 180 stores have no data for H2-2014, leading to a gap in the dataset |
| q09 | True | 1 | False | True | CORRECT | 180 stores are missing data for the second half of 2014. This information comes from the documentation excerpt [1], which states that 180 stores have no rows fr |
| q10 | True | 3 | False | True | CORRECT | 33,121 store-days are missing in total from the sales data. This information comes from the description of the H2-2014 gap, which states that 180 stores have no |
| q11 | True | 1 | False | True | PARTIAL | Rows where a store is marked open but reports zero sales are kept in the fact table, flagged with `is_zero_sales_while_open`, counted in `ops.load_audit`, and e |
| q12 | True | 1 | False | True | PARTIAL | A trading day is a store-day where `is_open` is true and `is_zero_sales_while_open` is false. It represents the number of days a store was open and recorded non |
| q13 | True | 2 | False | True | CORRECT | The momentum factor gets a weight of 0.25 [2]. |
| q14 | True | 1 | False | True | CORRECT | Revenue scale has a weight of 0.40 in the tier score. [1][2] |
| q15 | True | 1 | False | True | CORRECT | Customer traffic is not used as a tiering factor because revenue = traffic × basket, and scoring both traffic and basket would count traffic twice. Measured Spe |
| q16 | True | 1 | False | True | PARTIAL | Based on the information provided in [1], 20% of the stores end up in tier A. [1] |
| q17 | True | 1 | False | True | CORRECT | A store is flagged as 'borderline' if it keeps its tier in fewer than 80% of 1,000 re-weighted scenarios, as indicated in [2]. Specifically, 357 stores keep the |
| q18 | True | 1 | False | True | CORRECT | 357 stores are flagged as borderline. [3] |
| q19 | True | 1 | False | False | PARTIAL | Store 236 is in tier B rather than tier A because it is effectively on the A/B line, scoring 64.72 out of 100. It is ranked 224th out of 1,115 stores, with tier |
| q20 | True | 2 | False | True | CORRECT | Store 1 is in tier D. [2] |
| q21 | True | 1 | False | False | PARTIAL | I don't know based on the pipeline documentation. [1] |
| q22 | True | 1 | False | False | INCORRECT | I don't know based on the pipeline documentation. [1] |
| q23 | True | 1 | False | True | CORRECT | The Mann–Whitney U test was used to compare stores near and far from competitors. [1] |
| q24 | True | 1 | False | False | INCORRECT | I don't know based on the pipeline documentation. [1] |
| q25 | True | 1 | False | True | CORRECT | `extract_*` tasks retry twice, 30 seconds apart. [1] |
| q26 | True | 1 | False | True | PARTIAL | Data-quality tasks never retry because a data problem doesn't fix itself in 30 seconds [1]. |
| q27 | True | 1 | False | False | INCORRECT | The row volume change check compares against the minimum and maximum number of rows specified in the contract. For the sales source, the limit is 1,000,000 to 1 |
| q28 | True | 1 | False | True | CORRECT | The table that records failed pipeline tasks is `ops.pipeline_alerts` [1]. |
| q29 | True | 1 | False | True | CORRECT | ops.load_audit contains the following columns: batch_id, source, source_sha256, rows_in, rows_loaded, issues, loaded_at. [1] |
| q30 | True | 1 | False | True | CORRECT | A parity check can report three status values: `pass`, `known`, and `fail` [1]. |
| q31 | True | 3 | False | True | CORRECT | To register a known upstream data issue so that the parity check doesn't fail, you need to add a rule to the `contracts/parity.yml` file. This involves creating |
| q32 | True | 1 | False | True | INCORRECT | The freshness check allows the latest `sales_date` to lag behind the `as_of_date` by more than 3 days. [1] |
| q33 | True | None | False | False | CORRECT | I don't know based on the pipeline documentation. |
| q34 | True | 3 | False | False | PARTIAL | The store_states data comes from the source table raw.store_states, which is loaded by the Airflow DAG. [3] |
| q35 | True | 1 | False | True | PARTIAL | The state code HB,NI means Bremen or Lower Saxony, which is an ambiguous code in the source data [3]. [4] |
| q36 | True | 1 | False | True | PARTIAL | Based on the information provided, 22 stores have an ambiguous state code. This can be determined from the excerpt [1], which states that the column `is_state_a |
| q37 | True | 1 | False | True | PARTIAL | Promo refers to a short-term promotion that runs on specific days (Monday-Friday) within a single week, while Promo2 is a continuing and recurring promotion tha |
| q38 | True | 2 | False | False | PARTIAL | The dbt data test `assert_every_store_has_features` ensures every store in the dimension gets a tiering feature row. However, there is no specific range mention |
| q39 | True | 1 | False | True | PARTIAL | The pipeline verifies that the fact table matches the raw sales data through the `assert_fct_reconciles_to_raw` dbt data test. This test ensures that per month, |
| q40 | True | 1 | False | True | PARTIAL | A NULL competition_distance_m means the distance to the nearest competitor store is unknown or blank [1]. |
| q41 | True | 1 | False | True | PARTIAL | September is written as "Sept" in the source PromoInterval field [1]. Cleaning normalises it to "Sep" [1]. |
| u01 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.61 is below the 0.62 threshold). Closest documented topics: Data dicti |
| u02 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.54 is below the 0.62 threshold). Closest documented topics: Data dicti |
| u03 | False | None | False | True | CORRECT | I don't know based on the pipeline documentation. |
| u04 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.62 is below the 0.62 threshold). Closest documented topics: Data dicti |
| u05 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.53 is below the 0.62 threshold). Closest documented topics: Data dicti |
| u06 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.52 is below the 0.62 threshold). Closest documented topics: Model mart |
| u07 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.60 is below the 0.62 threshold). Closest documented topics: Column mar |
| u08 | False | None | True | True | CORRECT | I couldn't find a confident answer in the pipeline documentation (best match similarity 0.60 is below the 0.62 threshold). Closest documented topics: Model mart |
