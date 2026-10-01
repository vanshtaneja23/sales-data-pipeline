from __future__ import annotations

from datetime import date

from sales_pipeline.config import REPO_ROOT
from sales_pipeline.parity import Gap, classify_gaps, key_coverage_result, load_rules

RULES = [
    {"id": "H2", "date_from": date(2014, 7, 1), "date_to": date(2014, 12, 31), "max_stores": 2},
    {"id": "S988", "stores": [988], "date_from": date(2013, 1, 1), "date_to": date(2013, 1, 1),
     "max_stores": 1},
]


def h2_gap(store: int) -> Gap:
    return Gap(store, date(2014, 7, 1), date(2014, 12, 31), 184)


def test_no_gaps_passes():
    assert classify_gaps([], RULES).status == "pass"


def test_allowlisted_gaps_are_known_not_hidden():
    r = classify_gaps([h2_gap(13), h2_gap(20), Gap(988, date(2013, 1, 1), date(2013, 1, 1), 1)], RULES)
    assert r.status == "known"
    assert r.discrepancies == 369  # still counted and reported
    assert {"rule": "H2", "stores": 2} in r.sample


def test_new_gap_outside_rules_fails():
    new = Gap(5, date(2015, 3, 2), date(2015, 3, 4), 3)
    r = classify_gaps([h2_gap(13), new], RULES)
    assert r.status == "fail"
    assert r.sample == [
        {"store_id": 5, "gap_start": date(2015, 3, 2), "gap_end": date(2015, 3, 4), "days": 3}
    ]


def test_gap_partially_outside_window_fails():
    # Starts inside the known window but runs past it: not explained.
    r = classify_gaps([Gap(13, date(2014, 12, 1), date(2015, 1, 10), 41)], RULES)
    assert r.status == "fail"


def test_store_specific_rule_does_not_cover_other_stores():
    r = classify_gaps([Gap(1, date(2013, 1, 1), date(2013, 1, 1), 1)], RULES)
    assert r.status == "fail"


def test_known_gap_growing_beyond_baseline_fails():
    r = classify_gaps([h2_gap(1), h2_gap(2), h2_gap(3)], RULES)  # max_stores is 2
    assert r.status == "fail" and "more stores than baselined" in r.detail


def test_key_coverage():
    assert key_coverage_result("k", "a", "b", [], []).status == "pass"
    r = key_coverage_result("k", "sales", "stores", [9999], [])
    assert r.status == "fail" and r.discrepancies == 1 and "1 store(s) only in sales" in r.detail


def test_repo_rules_parse_with_real_dates():
    rules = load_rules(REPO_ROOT / "contracts" / "parity.yml")
    assert {r["id"] for r in rules} == {"KAGGLE-H2-2014-GAP", "STORE-988-2013-01-01"}
    assert all(isinstance(r["date_from"], date) for r in rules)
