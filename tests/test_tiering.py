from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sales_analytics.tiering import (
    FACTORS,
    Factor,
    assign_tiers,
    explain_store,
    percentile_ranks,
    score_stores,
    validate_weights,
    weight_sensitivity,
)


@pytest.fixture
def features() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 100
    return pd.DataFrame(
        {
            "avg_daily_sales": rng.normal(7000, 2000, n),
            "sales_growth_yoy": rng.normal(0.03, 0.08, n),
            "weekly_sales_cv": rng.uniform(0.09, 0.5, n),
            "avg_sales_per_customer": rng.normal(9.5, 2, n),
        },
        index=pd.Index(range(1, n + 1), name="store_id"),
    )


def test_shipped_weights_are_valid_and_sum_to_one():
    validate_weights()
    assert sum(f.weight for f in FACTORS) == pytest.approx(1.0)


def test_bad_weights_rejected():
    with pytest.raises(ValueError, match="sum to 1.0"):
        validate_weights((Factor("a", "x", 0.5, True, ""),))


def test_lower_is_better_factor_is_inverted(features):
    pct = percentile_ranks(features)
    calmest = features["weekly_sales_cv"].idxmin()
    assert pct.loc[calmest, "stability"] == 1.0


def test_null_factor_value_fails_loudly(features):
    features.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="null factor values"):
        percentile_ranks(features)


def test_tier_shares(features):
    tiers = score_stores(features)["tier"].value_counts()
    assert tiers.to_dict() == {"A": 20, "B": 30, "C": 30, "D": 20}


def test_tiers_monotone_in_score(features):
    scored = score_stores(features)
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    by_rank = scored.sort_values("rank")["tier"].map(order).to_numpy()
    assert (np.diff(by_rank) >= 0).all()


def test_points_sum_to_score(features):
    scored = score_stores(features)
    points = scored[[f"{f.name}_points" for f in FACTORS]].sum(axis=1)
    np.testing.assert_allclose(points, scored["score"])


def test_best_on_every_factor_is_tier_a():
    feats = pd.DataFrame(
        {"avg_daily_sales": [1, 2, 3, 4, 5], "sales_growth_yoy": [1, 2, 3, 4, 5],
         "weekly_sales_cv": [5, 4, 3, 2, 1], "avg_sales_per_customer": [1, 2, 3, 4, 5]},
        index=pd.Index([10, 20, 30, 40, 50], name="store_id"),
    )
    scored = score_stores(feats)
    assert scored.loc[50, "tier"] == "A" and scored.loc[50, "score"] == pytest.approx(100)
    assert scored.loc[10, "tier"] == "D"


def test_assign_tiers_breaks_ties_deterministically():
    scores = pd.Series([50.0] * 10, index=range(10))
    assert assign_tiers(scores).tolist() == assign_tiers(scores).tolist()


def test_explain_store_is_consistent(features):
    scored = score_stores(features)
    store = scored.index[scored["tier"] == "B"][0]
    e = explain_store(store, features, scored)
    assert e["tier"] == "B" and e["next_tier"] == "A"
    assert sum(b["points"] for b in e["breakdown"]) == pytest.approx(e["score"], abs=0.05)
    assert e["points_to_next_tier"] > 0
    assert e["weakest_factor"] in {f.name for f in FACTORS}


def test_top_store_has_no_next_tier(features):
    scored = score_stores(features)
    e = explain_store(scored.index[0], features, scored)
    assert e["tier"] == "A" and e["next_tier"] is None and e["points_to_next_tier"] is None


def test_sensitivity_is_bounded_and_reproducible(features):
    a = weight_sensitivity(features, n_draws=50)
    b = weight_sensitivity(features, n_draws=50)
    assert a.between(0, 1).all()
    pd.testing.assert_series_equal(a, b)
