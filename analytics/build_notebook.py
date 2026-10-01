"""Generate analytics/store_tiering.ipynb from reviewable Python source.

    make notebook   # builds, then executes against the warehouse so outputs are real

Keeping the notebook as code here makes diffs readable; the executed .ipynb is committed so the
outputs (charts, test statistics) are visible on GitHub without running anything.
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent

cells: list[tuple[str, str]] = []


def md(text: str) -> None:
    cells.append(("md", text.strip()))


def code(text: str) -> None:
    cells.append(("code", text.strip()))


md("""
# Store tiering — Rossmann (1,115 stores, 2013-01 → 2015-07)

**Goal.** Rank every store into tiers A–D with a transparent, weighted multi-factor score that a
regional manager can challenge store by store.

**Inputs.** `marts.mart_store_features` (built and tested by dbt in the Airflow DAG),
`marts.dim_store`, `marts.fct_sales_daily`. Nothing in this notebook re-derives a metric the
warehouse already defines, so the numbers here match the documented data dictionary.

Sections: 1) data & coverage · 2) EDA · 3) hypothesis test · 4) tiering model · 5) sensitivity ·
6) defending a single store · 7) limitations.
""")

code("""
import json, os, sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg
from scipy import stats

ROOT = Path.cwd().parent if Path.cwd().name == "analytics" else Path.cwd()
sys.path.insert(0, str(ROOT / "src"))
from sales_analytics.tiering import FACTORS, TIER_SHARES, explain_store, score_stores, weight_sensitivity

FIG = ROOT / "analytics" / "figures"
FIG.mkdir(parents=True, exist_ok=True)
RESULTS: dict = {}
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False})

conn = psycopg.connect(
    host=os.environ.get("WAREHOUSE_HOST", "localhost"), port=os.environ.get("WAREHOUSE_PORT", "5433"),
    dbname=os.environ.get("WAREHOUSE_DB", "warehouse"), user=os.environ["WAREHOUSE_USER"],
    password=os.environ["WAREHOUSE_PASSWORD"],
)
q = lambda sql: pd.read_sql_query(sql, conn)  # noqa: E731
""")

md("## 1. Data & coverage")
code("""
features = q("select * from marts.mart_store_features").set_index("store_id").sort_index()
stores = q("select * from marts.dim_store").set_index("store_id").sort_index()
coverage = q('''
    select count(*) as rows, count(distinct store_id) as stores,
           min(sales_date) as first_day, max(sales_date) as last_day,
           sum(case when is_open then 1 else 0 end) as open_days
    from marts.fct_sales_daily''')
RESULTS["coverage"] = coverage.iloc[0].astype(str).to_dict()
print(coverage.T.to_string(header=False))
print("\\nstores missing days:", features["days_missing"].value_counts().sort_index().to_dict())
""")

md("""
180 stores are missing every day from 2014-07-01 to 2014-12-31 (and store 988 misses one day).
The pipeline's parity check baselines this gap as a known upstream defect. It is why the
**momentum** factor compares H1-2015 with H1-2014 rather than full years: H1 is the latest
like-for-like window every store has.
""")
code("""
monthly = q('''
    select date_trunc('month', sales_date)::date as month,
           count(distinct store_id) as stores_reporting,
           sum(sales_amount) / 1e6 as sales_m
    from marts.fct_sales_daily group by 1 order by 1''')
fig, ax = plt.subplots(figsize=(9, 3.2))
ax.plot(monthly["month"], monthly["sales_m"], color="#3b6ea5")
ax.set_ylabel("monthly sales (EUR m)")
ax2 = ax.twinx(); ax2.plot(monthly["month"], monthly["stores_reporting"], color="#c0504d", ls="--")
ax2.set_ylabel("stores reporting", color="#c0504d"); ax2.spines["top"].set_visible(False)
ax.axvspan(pd.Timestamp("2014-07-01"), pd.Timestamp("2014-12-31"), color="grey", alpha=.15)
ax.set_title("Monthly sales and stores reporting (shaded: H2-2014 upstream gap)")
fig.tight_layout(); fig.savefig(FIG / "monthly_sales_coverage.png"); plt.show()
""")

md("## 2. Exploratory analysis")
code("""
dow = q('''
    select day_of_week, avg(sales_amount) as avg_sales,
           avg(case when is_promo then 1.0 else 0 end) as promo_share
    from marts.fct_sales_daily where is_open and not is_zero_sales_while_open group by 1 order by 1''')
fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
axes[0].bar(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], dow["avg_sales"], color="#3b6ea5")
axes[0].set_title("Avg sales per trading day by weekday")
by_type = features.join(stores["store_type"])
by_type.boxplot("avg_daily_sales", by="store_type", ax=axes[1], grid=False)
axes[1].set_title("Avg daily sales by store type"); axes[1].set_xlabel("")
by_assort = features.join(stores["assortment_level"])
by_assort.boxplot("avg_daily_sales", by="assortment_level", ax=axes[2], grid=False)
axes[2].set_title("…by assortment"); axes[2].set_xlabel("")
fig.suptitle(""); fig.tight_layout(); fig.savefig(FIG / "eda_weekday_type_assortment.png"); plt.show()
print(by_type.groupby("store_type")["avg_daily_sales"].agg(["count", "median"]).round(0))
""")

code("""
cols = {f.name: f.column for f in FACTORS} | {"traffic (not scored)": "avg_daily_customers"}
fig, axes = plt.subplots(1, len(cols), figsize=(15, 2.8))
for ax, (name, col) in zip(axes, cols.items()):
    ax.hist(features[col], bins=40, color="#3b6ea5"); ax.set_title(name, fontsize=9); ax.set_yticks([])
fig.tight_layout(); fig.savefig(FIG / "factor_distributions.png"); plt.show()
features[list(cols.values())].describe().T.round(3)
""")

md("""
### Why traffic is *not* a scoring factor
Revenue per day ≈ customers per day × sales per customer. If revenue, traffic and basket were all
scored, traffic would be counted twice (directly and inside revenue). The correlation matrix
confirms it: revenue and traffic move together, while basket carries information revenue does not.
""")
code("""
corr = features[list(cols.values())].corr(method="spearman")
RESULTS["spearman_revenue_traffic"] = round(float(corr.loc["avg_daily_sales", "avg_daily_customers"]), 3)
RESULTS["spearman_revenue_basket"] = round(float(corr.loc["avg_daily_sales", "avg_sales_per_customer"]), 3)
corr.round(2)
""")

md("""
## 3. Hypothesis test: does a nearby competitor mean lower sales?

The intuitive story is that a competitor next door steals sales. The data hint otherwise
(median sales are *higher* for stores with a competitor under 500 m). We test it formally,
deciding the method before looking at the p-value:

* **Groups** — stores whose nearest competitor is `< 500 m` vs `>= 10 km` (`dim_store.competition_distance_band`).
* **Metric** — `avg_daily_sales` (per trading day, from `mart_store_features`).
* **H0** — the two groups' sales distributions are the same. **H1** — they differ (two-sided).
* **Test** — Mann–Whitney U: store sales are right-skewed with outliers, so a rank test is safer than a t-test.
* **α = 0.05.** Effect size reported as rank-biserial correlation and a bootstrap 95% CI on the
  difference in medians, because "significant" alone says nothing about size.
""")
code("""
data = features.join(stores["competition_distance_band"]).join(stores["store_type"])
near = data.loc[data["competition_distance_band"] == "< 500 m", "avg_daily_sales"].astype(float)
far = data.loc[data["competition_distance_band"] == ">= 10 km", "avg_daily_sales"].astype(float)

u, p = stats.mannwhitneyu(near, far, alternative="two-sided")
rank_biserial = 2 * u / (len(near) * len(far)) - 1

rng = np.random.default_rng(42)
boot = np.array([np.median(rng.choice(near, len(near))) - np.median(rng.choice(far, len(far)))
                 for _ in range(10_000)])
ci = np.percentile(boot, [2.5, 97.5])

RESULTS["hypothesis_competition"] = {
    "n_near": int(len(near)), "n_far": int(len(far)),
    "median_near": round(float(near.median()), 0), "median_far": round(float(far.median()), 0),
    "median_diff": round(float(near.median() - far.median()), 0),
    "median_diff_ci95": [round(float(ci[0]), 0), round(float(ci[1]), 0)],
    "mann_whitney_u": float(u), "p_value": float(p), "rank_biserial": round(float(rank_biserial), 3),
    "median_customers_near": round(float(data.loc[near.index, "avg_daily_customers"].median()), 0),
    "median_customers_far": round(float(data.loc[far.index, "avg_daily_customers"].median()), 0),
}
pd.Series(RESULTS["hypothesis_competition"])
""")
code("""
fig, ax = plt.subplots(figsize=(6, 3.2))
ax.boxplot([near, far], tick_labels=["< 500 m", ">= 10 km"], showfliers=False)
ax.set_ylabel("avg daily sales (EUR)"); ax.set_title("Avg daily sales by nearest-competitor distance")
fig.tight_layout(); fig.savefig(FIG / "competition_hypothesis.png"); plt.show()

# Confounding check: is the difference just a store-type mix effect?
strat = (data[data["competition_distance_band"].isin(["< 500 m", ">= 10 km"])]
         .groupby(["store_type", "competition_distance_band"])["avg_daily_sales"]
         .agg(["count", "median"]).unstack().round(0))
RESULTS["hypothesis_competition_by_type"] = json.loads(strat.to_json())
strat
""")
code("""
h = RESULTS["hypothesis_competition"]
verdict = "reject H0" if h["p_value"] < 0.05 else "fail to reject H0"
print(f"Mann-Whitney U = {h['mann_whitney_u']:.0f}, p = {h['p_value']:.4f} -> {verdict} at alpha = 0.05")
print(f"median(<500 m) - median(>=10 km) = {h['median_diff']:.0f} EUR/day, 95% CI {h['median_diff_ci95']}")
print(f"rank-biserial r = {h['rank_biserial']} (|r| < 0.1 negligible, ~0.3 medium)")
RESULTS["hypothesis_competition"]["verdict"] = verdict

# Does the direction survive within each store type? (store types with both groups present)
med = strat["median"].dropna()
flips = {t: ("near > far" if r["< 500 m"] > r[">= 10 km"] else "near < far") for t, r in med.iterrows()}
mix_near = data.loc[near.index, "store_type"].value_counts(normalize=True).round(2).to_dict()
mix_far = data.loc[far.index, "store_type"].value_counts(normalize=True).round(2).to_dict()
RESULTS["hypothesis_competition"].update(direction_by_store_type=flips, type_mix_near=mix_near,
                                         type_mix_far=mix_far)
print("direction within store type:", flips)
print("store-type mix  near:", mix_near, " far:", mix_far)
""")
md("""
**Reading it.** This is observational data. Stores with a competitor under 500 m are
disproportionately in dense locations that also bring foot traffic (compare `median_customers_near`
vs `median_customers_far` above), and the two groups have a different store-type mix. The
within-type comparison checks whether the pooled direction survives once type is held fixed; when
it does not, the pooled gap is at least partly a mix effect (Simpson's-paradox pattern). So the test
can tell us
whether the *association* is real, not that competitors cause higher sales. The stratified table
above is the first check on that; a causal answer would need something like stores whose
competitor *opened* during the window (difference-in-differences on `competition_open_since_date`).
**Implication for tiering:** competition distance is not used as a scoring factor — its direction
is ambiguous and it describes location, not store performance.
""")

md("## 4. Tiering model")
code("""
weights = pd.DataFrame([{"factor": f.name, "metric": f.column, "weight": f.weight,
                         "direction": "higher is better" if f.higher_is_better else "lower is better",
                         "why": f.rationale} for f in FACTORS])
RESULTS["weights"] = {f.name: f.weight for f in FACTORS}
RESULTS["tier_shares"] = dict(TIER_SHARES)
weights
""")
code("""
scored = score_stores(features)
bounds = scored.groupby("tier")["score"].agg(["count", "min", "max", "median"]).round(1)
RESULTS["tier_score_ranges"] = json.loads(bounds.to_json())
bounds
""")
code("""
profile = (features.join(scored[["tier"]]).groupby("tier")
           [[f.column for f in FACTORS] + ["avg_daily_customers"]].median().round(3))
mix = pd.crosstab(scored["tier"], stores["store_type"], normalize="index").round(2)
display(profile); display(mix)
""")
code("""
fig, ax = plt.subplots(figsize=(8, 3.2))
colors = {"A": "#2e7d32", "B": "#7cb342", "C": "#f9a825", "D": "#c62828"}
for t in "ABCD":
    s = scored.loc[scored["tier"] == t, "score"]
    ax.hist(s, bins=np.arange(0, 101, 2), color=colors[t], label=f"Tier {t} (n={len(s)})")
ax.set_xlabel("score (0-100)"); ax.set_ylabel("stores"); ax.legend(frameon=False)
ax.set_title("Score distribution by tier")
fig.tight_layout(); fig.savefig(FIG / "tier_scores.png"); plt.show()
""")

md("""
## 5. Sensitivity: does a store's tier hinge on the exact weights?
Weights are a judgement call, so we stress them: 1,000 random weight vectors drawn from a
Dirichlet centred on the chosen weights (each weight typically moves by a few points), re-tier
every store each time, and measure how often it keeps its tier.
""")
code("""
stability = weight_sensitivity(features, n_draws=1000)
scored = scored.join(stability)
# Publish the uncertainty instead of hiding it: a store that keeps its tier in < 80% of plausible
# weightings is reported as borderline (e.g. "B, borderline A/B") rather than as a crisp tier.
scored["is_borderline"] = scored["tier_stability"] < 0.8
RESULTS["sensitivity"] = {
    "borderline_stores": int(scored["is_borderline"].sum()),
    "share_stores_stable_ge_90pct": round(float((stability >= 0.9).mean()), 3),
    "share_stores_stable_ge_80pct": round(float((stability >= 0.8).mean()), 3),
    "median_stability": round(float(stability.median()), 3),
    "n_draws": 1000,
}
print(RESULTS["sensitivity"])
scored.groupby("tier")["tier_stability"].describe()[["mean", "min", "50%"]].round(3)
""")
code("""
fig, ax = plt.subplots(figsize=(7, 3))
ax.scatter(scored["score"], scored["tier_stability"], s=6, c=scored["tier"].map(colors))
for t, lo in scored.groupby("tier")["score"].min().items():
    ax.axvline(lo, color="grey", lw=.6, ls=":")
ax.set_xlabel("score"); ax.set_ylabel("share of draws keeping tier")
ax.set_title("Tier stability under weight perturbation (dips = stores near a boundary)")
fig.tight_layout(); fig.savefig(FIG / "tier_stability.png"); plt.show()
""")

md("""
## 6. Defending a single store's tier
`explain_store` returns, for one store: its raw metric values, percentile on each factor, the points
each factor contributed (weight × percentile × 100), the score floor of its tier, how many points
it needs for the next tier, and its weakest factor relative to that factor's weight.
""")
code("""
def show(store_id: int) -> dict:
    e = explain_store(store_id, features, scored)
    e["tier_stability"] = round(float(scored.loc[store_id, "tier_stability"]), 3)
    print(f"Store {e['store_id']}: tier {e['tier']}, rank {e['rank']}/{e['of']}, score {e['score']} "
          f"(tier floor {e['tier_floor']}); next tier {e['next_tier']} needs +{e['points_to_next_tier']} pts; "
          f"weakest factor: {e['weakest_factor']}; keeps tier in {e['tier_stability']:.0%} of weight draws")
    display(pd.DataFrame(e["breakdown"]).set_index("factor"))
    return e

# Boundary case: the tier-B store closest to tier A -- the one a manager is most likely to dispute.
b_stores = scored[scored["tier"] == "B"]
boundary_store = int(b_stores["score"].idxmax())
RESULTS["example_boundary_store"] = show(boundary_store)
""")
code("""
# A reference case: store 1 (first store in the dataset).
RESULTS["example_store_1"] = show(1)
""")
code("""
e = RESULTS["example_boundary_store"]
b = pd.DataFrame(e["breakdown"])
fig, ax = plt.subplots(figsize=(7, 2.8))
ax.barh(b["factor"], b["max_points"], color="#e0e0e0", label="max possible")
ax.barh(b["factor"], b["points"], color="#3b6ea5", label="earned")
ax.set_xlabel("points"); ax.legend(frameon=False, loc="lower right")
ax.set_title(f"Store {e['store_id']}: {e['score']} pts (tier {e['tier']}, A needs +{e['points_to_next_tier']})")
fig.tight_layout(); fig.savefig(FIG / "defend_store.png"); plt.show()
""")

md("""
**How to defend it in one breath:** *"Store X is tier B because it scores N/100: it earned P points
of a possible 40 on revenue, … It's M points short of tier A; its weakest lever is <factor>. The tier
is robust — it holds in K% of re-weighted scenarios — so this isn't an artefact of our exact weights."*
If a manager disputes it, the conversation moves to the specific factor and the data behind it,
not to the model as a black box.
""")

md("""
## 7. Limitations
* **Relative tiers.** A–D are quantile-based, so 20% of stores are always tier D even if every store
  improves. Fine for prioritising attention; not a pass/fail standard.
* **Weights are judgement**, made explicit and stress-tested (section 5), not learned from an outcome.
  With a target (e.g. future profitability) they could be fit and validated out of sample.
* **No profitability data**: revenue is not margin. Rent, staff and shrink are not in the source.
* **Momentum uses one window** (H1-15 vs H1-14); a store refurbished during H1-2014 looks like a
  growth star. Closures over 7 days could be detected from `fct_sales_daily` and excluded.
* **Hypothesis test is observational**: association, not causation (see section 3).
""")
code("""
scored_out = scored.reset_index()[["store_id", "rank", "tier", "score", "tier_stability", "is_borderline"]
                                  + [f"{f.name}_points" for f in FACTORS]]
scored_out.round(3).to_csv(ROOT / "analytics" / "store_tiers.csv", index=False)
(ROOT / "analytics" / "results.json").write_text(json.dumps(RESULTS, indent=2, default=str))
conn.close()
print("wrote analytics/store_tiers.csv and analytics/results.json")
""")


def build() -> Path:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src)
                for kind, src in cells]
    out = HERE / "store_tiering.ipynb"
    nbf.write(nb, out)
    return out


if __name__ == "__main__":
    print(build())
