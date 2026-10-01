from __future__ import annotations

import pandas as pd
import pytest
from conftest import make_sales, make_stores

from sales_pipeline.clean import DataContractError, clean_sales, clean_store_states, clean_stores


class TestCleanSales:
    def test_types_and_renames(self, sales_contract):
        r = clean_sales(make_sales(), sales_contract)
        row = r.frame.iloc[0]
        assert list(r.frame.columns) == [c.name for c in sales_contract.all_columns]
        assert row["store_id"] == 1 and row["sales_amount"] == 5263
        assert row["sales_date"] == pd.Timestamp("2015-07-31")
        assert bool(row["is_open"]) is True and bool(row["is_school_holiday"]) is True

    @pytest.mark.parametrize(("code", "expected"), [
        ("0", "none"), ("a", "public"), ("b", "easter"), ("c", "christmas"),
    ])
    def test_state_holiday_decoded(self, sales_contract, code, expected):
        r = clean_sales(make_sales([{"StateHoliday": code}]), sales_contract)
        assert r.frame.loc[0, "state_holiday"] == expected

    def test_unknown_state_holiday_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="state_holiday.*allowed"):
            clean_sales(make_sales([{"StateHoliday": "z"}]), sales_contract)

    def test_non_numeric_sales_fails_with_example(self, sales_contract):
        with pytest.raises(DataContractError, match=r"sales_amount: 1 non-numeric.*'12k'"):
            clean_sales(make_sales([{"Sales": "12k"}]), sales_contract)

    def test_negative_sales_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="sales_amount.*below min"):
            clean_sales(make_sales([{"Sales": "-5"}]), sales_contract)

    def test_empty_required_value_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="customer_count.*empty"):
            clean_sales(make_sales([{"Customers": ""}]), sales_contract)

    def test_bad_date_format_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="sales_date.*YYYY-MM-DD"):
            clean_sales(make_sales([{"Date": "31/07/2015"}]), sales_contract)

    def test_weekday_mismatch_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="day_of_week.*disagree"):
            clean_sales(make_sales([{"DayOfWeek": "3"}]), sales_contract)

    def test_zero_sales_while_open_flagged_not_dropped(self, sales_contract):
        r = clean_sales(make_sales([{}, {"Store": "2", "Sales": "0", "Customers": "0"}]), sales_contract)
        assert r.rows_out == 2
        assert r.issues["zero_sales_while_open"] == 1
        assert r.frame["is_zero_sales_while_open"].tolist() == [False, True]

    def test_exact_duplicates_dropped_and_counted(self, sales_contract):
        r = clean_sales(make_sales([{}, {}]), sales_contract)
        assert r.rows_in == 2 and r.rows_out == 1
        assert r.issues["exact_duplicates_dropped"] == 1

    def test_conflicting_duplicate_key_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="conflicting"):
            clean_sales(make_sales([{}, {"Sales": "1"}]), sales_contract)

    def test_missing_source_column_fails(self, sales_contract):
        with pytest.raises(DataContractError, match="missing columns.*Promo"):
            clean_sales(make_sales().drop(columns=["Promo"]), sales_contract)


class TestCleanStores:
    def test_sept_normalised_and_counted(self, stores_contract):
        raw = make_stores([{
            "Promo2": "1", "Promo2SinceWeek": "14", "Promo2SinceYear": "2011",
            "PromoInterval": "Mar,Jun,Sept,Dec",
        }])
        r = clean_stores(raw, stores_contract)
        assert r.frame.loc[0, "promo2_interval"] == "Mar,Jun,Sep,Dec"
        assert r.issues["promo_interval_sept_normalised"] == 1

    def test_missing_competition_distance_is_null_not_zero(self, stores_contract):
        r = clean_stores(make_stores([{"CompetitionDistance": ""}]), stores_contract)
        assert pd.isna(r.frame.loc[0, "competition_distance_m"])
        assert r.issues["competition_distance_missing"] == 1

    def test_invalid_month_fails(self, stores_contract):
        with pytest.raises(DataContractError, match="competition_open_since_month.*above max"):
            clean_stores(make_stores([{"CompetitionOpenSinceMonth": "13"}]), stores_contract)

    def test_promo2_inconsistency_counted(self, stores_contract):
        r = clean_stores(make_stores([{"Promo2": "1"}]), stores_contract)  # promo2 but no dates
        assert r.issues["promo2_flag_inconsistent"] == 1


def test_store_states_ambiguous_flag(states_contract):
    raw = pd.DataFrame({"Store": ["1", "2"], "State": ["HE", "HB,NI"]}, dtype=str)
    r = clean_store_states(raw, states_contract)
    assert r.frame["is_state_ambiguous"].tolist() == [False, True]
    assert r.issues["ambiguous_state"] == 1


def test_store_states_unknown_code_fails(states_contract):
    raw = pd.DataFrame({"Store": ["1"], "State": ["XX"]}, dtype=str)
    with pytest.raises(DataContractError, match="state_code"):
        clean_store_states(raw, states_contract)
