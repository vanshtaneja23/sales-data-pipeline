"""Weighted multi-factor store tiering.

score = 100 * sum_i( weight_i * percentile_rank_i )

* Percentile ranks put every factor on the same 0..1 scale and stop one extreme store from
  dominating a factor (a z-score would let a single outlier stretch the scale).
* Factors where lower is better (weekly sales volatility) are ranked descending.
* Tiers are relative: A = top 20% of scores, B = next 30%, C = next 30%, D = bottom 20%.

Traffic (avg_daily_customers) is deliberately NOT a factor: revenue = traffic x basket, so
scoring revenue, traffic and basket together would double-count traffic. We score revenue and
basket (correlation with revenue ~0, so it adds independent information) and report traffic
only as context.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Factor:
    name: str
    column: str
    weight: float
    higher_is_better: bool
    rationale: str


FACTORS: tuple[Factor, ...] = (
    Factor("revenue_scale", "avg_daily_sales", 0.40, True,
           "Average sales per trading day: the outcome the business ultimately cares about."),
    Factor("momentum", "sales_growth_yoy", 0.25, True,
           "H1-2015 vs H1-2014 like-for-like growth: where the store is heading, not just where it is."),
    Factor("stability", "weekly_sales_cv", 0.20, False,
           "Coefficient of variation of weekly sales: predictable stores are cheaper to staff and stock."),
    Factor("basket", "avg_sales_per_customer", 0.15, True,
           "Sales per customer: visit quality, the part of revenue that traffic does not explain."),
)

# Cumulative share of stores from the top: A = top 20%, B = next 30%, C = next 30%, D = rest.
TIER_SHARES: tuple[tuple[str, float], ...] = (("A", 0.20), ("B", 0.50), ("C", 0.80), ("D", 1.00))


def validate_weights(factors: tuple[Factor, ...] = FACTORS) -> None:
    total = sum(f.weight for f in factors)
    if not np.isclose(total, 1.0):
        raise ValueError(f"factor weights must sum to 1.0, got {total}")
    if any(f.weight <= 0 for f in factors):
        raise ValueError("every factor weight must be positive")


def percentile_ranks(features: pd.DataFrame, factors: tuple[Factor, ...] = FACTORS) -> pd.DataFrame:
    """0..1 percentile rank per factor (1 = best), ties get their average rank."""
    missing = [f.column for f in factors if f.column not in features]
    if missing:
        raise KeyError(f"features missing columns {missing}")
    nulls = features[[f.column for f in factors]].isna().sum()
    if nulls.any():
        raise ValueError(
            f"null factor values would silently rank a store; fix upstream: {nulls[nulls > 0].to_dict()}"
        )
    return pd.DataFrame(
        {f.name: features[f.column].astype(float).rank(pct=True, ascending=f.higher_is_better)
         for f in factors},
        index=features.index,
    )


def assign_tiers(scores: pd.Series) -> pd.Series:
    """Map scores to tiers by rank position (A = best). Deterministic tie-break by index."""
    order = scores.sort_values(ascending=False, kind="mergesort").index
    position = pd.Series(np.arange(1, len(order) + 1) / len(order), index=order)
    tiers = pd.Series(index=order, dtype="object")
    lower = 0.0
    for tier, upper in TIER_SHARES:
        tiers[(position > lower) & (position <= upper)] = tier
        lower = upper
    return tiers.reindex(scores.index)


def score_stores(features: pd.DataFrame, factors: tuple[Factor, ...] = FACTORS) -> pd.DataFrame:
    """Return one row per store: percentile per factor, contribution points, score and tier.

    `features` must be indexed by store_id (marts.mart_store_features).
    """
    validate_weights(factors)
    pct = percentile_ranks(features, factors)
    contrib = pd.DataFrame(
        {f"{f.name}_points": 100 * f.weight * pct[f.name] for f in factors}, index=features.index
    )
    out = pd.concat([pct.add_suffix("_pct"), contrib], axis=1)
    out["score"] = contrib.sum(axis=1)
    out["tier"] = assign_tiers(out["score"])
    out["rank"] = out["score"].rank(ascending=False, method="first").astype(int)
    return out.sort_values("rank")


def tier_boundaries(scored: pd.DataFrame) -> dict[str, float]:
    """Lowest score that still earns each tier in this run."""
    return scored.groupby("tier")["score"].min().to_dict()


def explain_store(store_id: int, features: pd.DataFrame, scored: pd.DataFrame,
                  factors: tuple[Factor, ...] = FACTORS) -> dict:
    """Everything needed to defend one store's tier: inputs, ranks, points, and the gap to the next tier."""
    row = scored.loc[store_id]
    bounds = tier_boundaries(scored)
    tiers = [t for t, _ in TIER_SHARES]
    idx = tiers.index(row["tier"])
    next_up = tiers[idx - 1] if idx > 0 else None
    breakdown = [
        {
            "factor": f.name,
            "metric": f.column,
            "value": float(features.loc[store_id, f.column]),
            "percentile": round(float(row[f"{f.name}_pct"]) * 100, 1),
            "weight": f.weight,
            "points": round(float(row[f"{f.name}_points"]), 2),
            "max_points": round(100 * f.weight, 1),
        }
        for f in factors
    ]
    # The weakest factor relative to its weight is the most efficient lever to pull.
    weakest = min(breakdown, key=lambda b: b["points"] / b["max_points"])
    return {
        "store_id": int(store_id),
        "tier": row["tier"],
        "rank": int(row["rank"]),
        "of": len(scored),
        "score": round(float(row["score"]), 2),
        "tier_floor": round(bounds[row["tier"]], 2),
        "next_tier": next_up,
        "points_to_next_tier": round(bounds[next_up] - float(row["score"]), 2) if next_up else None,
        "breakdown": breakdown,
        "weakest_factor": weakest["factor"],
    }


def weight_sensitivity(features: pd.DataFrame, n_draws: int = 1000, concentration: float = 50.0,
                       seed: int = 7, factors: tuple[Factor, ...] = FACTORS) -> pd.Series:
    """Share of random weight perturbations under which each store keeps its baseline tier.

    Weights are drawn from Dirichlet(concentration * baseline_weights): centred on the chosen
    weights, each weight typically moves by a few percentage points. A store with stability
    near 1.0 has a tier that does not hinge on the exact weights.
    """
    pct = percentile_ranks(features, factors).to_numpy()
    base_w = np.array([f.weight for f in factors])
    baseline = assign_tiers(pd.Series(pct @ base_w, index=features.index))
    rng = np.random.default_rng(seed)
    draws = rng.dirichlet(concentration * base_w, size=n_draws)
    same = np.zeros(len(features))
    for w in draws:
        same += (assign_tiers(pd.Series(pct @ w, index=features.index)) == baseline).to_numpy()
    return pd.Series(same / n_draws, index=features.index, name="tier_stability")
