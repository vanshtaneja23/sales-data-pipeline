# Store tiering methodology

Code: `src/sales_analytics/tiering.py` · Notebook: `analytics/store_tiering.ipynb` ·
Output: `analytics/store_tiers.csv` · Measured numbers: `analytics/results.json`.
All figures below are copied from `results.json` (pinned source data, executed notebook).

## What the tier means

Every one of the 1,115 Rossmann stores gets a score from 0 to 100 and a tier:

| Tier | Share of stores | Score range in this run | Stores |
|---|---|---|---|
| A | top 20% | 64.8 – 93.4 | 223 |
| B | next 30% | 50.2 – 64.7 | 334 |
| C | next 30% | 35.6 – 50.2 | 335 |
| D | bottom 20% | 7.8 – 35.6 | 223 |

Tiers are **relative** (quantile-based): they rank stores against each other to prioritise
attention. They are not an absolute pass/fail standard; 20% of stores are always tier D.

## Score formula

```
score = 100 × Σ  weight_f × percentile_rank_f
```

Each factor is converted to a percentile rank across all stores (0–1, 1 = best), so factors
measured in euros, ratios and percentages become comparable, and a single extreme store cannot
stretch a factor's scale the way it would with z-scores.

## Factors and weights

| Factor | Metric (`marts.mart_store_features`) | Weight | Direction | Why |
|---|---|---|---|---|
| Revenue scale | `avg_daily_sales` | **0.40** | higher is better | Average sales per trading day; the outcome the business ultimately cares about. |
| Momentum | `sales_growth_yoy` | **0.25** | higher is better | Like-for-like growth, H1-2015 vs H1-2014. Where the store is heading, not just where it is. H1 is used because H2-2014 is missing for 180 stores upstream. |
| Stability | `weekly_sales_cv` | **0.20** | lower is better | Coefficient of variation of weekly sales over complete weeks. Predictable stores are cheaper to staff and stock. |
| Basket | `avg_sales_per_customer` | **0.15** | higher is better | Sales per customer: visit quality, the part of revenue that traffic doesn't explain. |

**Why traffic (`avg_daily_customers`) is not scored.** Revenue = traffic × basket. Scoring revenue,
traffic *and* basket would count traffic twice. Measured Spearman correlation with revenue:
traffic **0.797**, basket **0.068**, so basket adds independent information and traffic does not.

**Why competition distance is not scored.** Its direction is ambiguous (see the hypothesis test
below) and it describes a store's location, not its performance.

**How the weights were chosen.** They are an explicit judgement (revenue first, then trajectory,
then operability, then visit quality), written down and stress-tested, not fitted to an outcome,
because the source has no outcome to fit (no profit, no future performance label).

## Sensitivity: how much do tiers depend on the exact weights?

1,000 alternative weight vectors were drawn from a Dirichlet distribution centred on the chosen
weights (concentration 50, so each weight typically moves a few percentage points), and every
store was re-tiered each time.

* Median store keeps its tier in **90.8%** of draws.
* **51.9%** of stores keep their tier in ≥ 90% of draws; **68.0%** in ≥ 80%.
* **357 stores** keep it in < 80% of draws. They sit near a tier boundary and are flagged
  `is_borderline` in `store_tiers.csv`. Report them as e.g. "B (borderline A/B)" rather than
  pretending the line is crisp.

## How to defend a single store's tier

`explain_store(store_id, features, scored)` returns the store's raw metrics, its percentile on each
factor, the points each factor earned (weight × percentile × 100), the tier floor, the points needed
for the next tier, its weakest factor relative to that factor's weight, and (in the notebook) its
tier stability.

### Example 1: a clear case, store 1, tier D

| Factor | Value | Percentile | Points earned / max |
|---|---|---|---|
| Revenue scale | €4,759.10 / day | 13.9 | 5.56 / 40 |
| Momentum | −2.27% YoY | 10.9 | 2.74 / 25 |
| Stability | CV 0.1727 | 72.5 | 14.50 / 20 |
| Basket | €8.44 / customer | 31.8 | 4.78 / 15 |
| **Score** | | | **27.57** (rank 1,025 of 1,115) |

> "Store 1 is tier D because it scores 27.6/100. It is in the bottom 14% on revenue per day and the
> bottom 11% on growth (sales fell 2.3% year over year). It is genuinely steady week to week
> (72nd percentile), which is the only thing holding the score up. It needs +8.0 points to reach C,
> and the result is robust: it stays tier D in 98.4% of re-weighted scenarios."

### Example 2: the hardest case to defend, store 236, tier B, borderline

This is the tier-B store with the highest score, so it's the one a manager is most likely to dispute.

| Factor | Value | Percentile | Points earned / max |
|---|---|---|---|
| Revenue scale | €7,119.56 / day | 61.0 | 24.39 / 40 |
| Momentum | +8.89% YoY | 84.5 | 21.13 / 25 |
| Stability | CV 0.1698 | 75.0 | 15.00 / 20 |
| Basket | €8.23 / customer | 27.9 | 4.18 / 15 |
| **Score** | | | **64.72** (rank 224 of 1,115; tier A starts at rank 223) |

> "Store 236 is tier B by 0.04 points; it is effectively on the A/B line, and we say so: it keeps
> tier B in only 48.6% of re-weighted scenarios, so it is flagged borderline. Its growth and
> stability are A-grade. What keeps it out of A is basket size: customers spend €8.23 per visit,
> 28th percentile. That is the lever to discuss with the store, not the model."

The point of the defence is that the conversation moves to a specific factor and the data behind
it, not to the model as a black box.

## Hypothesis test run alongside the model

*Do stores with a competitor under 500 m sell less than stores whose nearest competitor is ≥ 10 km
away?* Mann–Whitney U, two-sided, α = 0.05, metric `avg_daily_sales`.

* n = 218 (< 500 m) vs 187 (≥ 10 km). Medians €7,017 vs €6,675; difference **+€341/day**, bootstrap
  95% CI **[−€214, +€784]**.
* U = 22,649, **p = 0.054** → **fail to reject H0** at α = 0.05. Rank-biserial r = 0.111 (small).
* Within store types the direction is **not consistent**: near > far for type a, but near < far
  for types c and d. The groups have different type mixes (near: 72% type a; far: 51% type a and
  43% type d), so the pooled gap is partly a store-mix effect.
* Conclusion: no evidence at α = 0.05 that a nearby competitor is associated with different sales,
  and the observational design could not show causation anyway. Hence competition distance is
  excluded from the tier score.

## Limitations

* Relative tiers, not absolute standards.
* Weights are judgement, made explicit and stress-tested, not learned from an outcome.
* Revenue is not profit; the source has no cost data.
* Momentum uses one window; a store refurbished during H1-2014 will look like a growth star.
* The hypothesis test is observational: association, not causation.
