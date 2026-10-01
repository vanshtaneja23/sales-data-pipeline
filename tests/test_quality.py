from __future__ import annotations

from datetime import date

import pytest
from conftest import make_sales

from sales_pipeline.quality import (
    CheckResult,
    DataQualityError,
    check_freshness,
    check_schema_drift,
    check_type_drift,
    check_volume,
    enforce,
)


class TestSchemaDrift:
    def test_identical_header_passes(self, sales_contract):
        assert check_schema_drift(sales_contract.source_columns, sales_contract).status == "pass"

    def test_removed_column_fails(self, sales_contract):
        header = [c for c in sales_contract.source_columns if c != "Customers"]
        r = check_schema_drift(header, sales_contract)
        assert r.status == "fail" and "missing=['Customers']" in r.observed

    def test_added_column_fails(self, sales_contract):
        r = check_schema_drift([*sales_contract.source_columns, "Returns"], sales_contract)
        assert r.status == "fail" and "added=['Returns']" in r.observed

    def test_renamed_column_reports_both_sides(self, sales_contract):
        header = ["Revenue" if c == "Sales" else c for c in sales_contract.source_columns]
        r = check_schema_drift(header, sales_contract)
        assert "missing=['Sales']" in r.observed and "added=['Revenue']" in r.observed

    def test_reordered_columns_pass_with_note(self, sales_contract):
        r = check_schema_drift(list(reversed(sales_contract.source_columns)), sales_contract)
        assert r.status == "pass" and "order" in r.detail


class TestTypeDrift:
    def test_clean_frame_passes(self, sales_contract):
        assert check_type_drift(make_sales(), sales_contract).status == "pass"

    def test_raw_holiday_codes_are_normalised_before_checking(self, sales_contract):
        assert check_type_drift(make_sales([{"StateHoliday": "a"}]), sales_contract).status == "pass"

    def test_reports_every_bad_column_not_just_first(self, sales_contract):
        r = check_type_drift(make_sales([{"Sales": "n/a", "Open": "yes"}]), sales_contract)
        assert r.status == "fail"
        assert "sales_amount" in r.detail and "is_open" in r.detail


class TestVolume:
    def test_within_bounds_and_stable(self, sales_contract):
        results = check_volume(1_017_209, sales_contract, previous_rows=1_017_209)
        assert [r.status for r in results] == ["pass", "pass"]

    def test_truncated_file_fails_bounds(self, sales_contract):
        assert check_volume(500_000, sales_contract, None)[0].status == "fail"

    def test_large_change_vs_previous_fails(self, sales_contract):
        # Inside absolute bounds but 7% above last load (> 5% limit).
        results = check_volume(1_088_000, sales_contract, previous_rows=1_017_209)
        assert results[0].status == "pass" and results[1].status == "fail"

    def test_first_load_warns_instead_of_silently_passing(self, sales_contract):
        assert check_volume(1_017_209, sales_contract, None)[1].status == "warn"


class TestFreshness:
    def test_fresh(self):
        assert check_freshness("sales", date(2015, 7, 31), date(2015, 7, 31), 3).status == "pass"

    def test_stale_fails(self):
        r = check_freshness("sales", date(2015, 7, 31), date(2015, 9, 30), 3)
        assert r.status == "fail" and "61d behind" in r.observed

    def test_future_dated_data_fails(self):
        assert check_freshness("sales", date(2015, 8, 5), date(2015, 7, 31), 3).status == "fail"

    def test_empty_table_fails(self):
        assert check_freshness("sales", None, date(2015, 7, 31), 3).status == "fail"


def test_enforce_raises_with_every_failure_listed():
    results = [
        CheckResult("a", "sales", "pass", "1", "1"),
        CheckResult("b", "sales", "fail", "2", "1", "boom"),
        CheckResult("c", "stores", "fail", "3", "1"),
        CheckResult("d", "stores", "warn", "4", "1"),
    ]
    with pytest.raises(DataQualityError, match="2 data quality check") as exc:
        enforce(results)
    assert [f.check for f in exc.value.failures] == ["b", "c"]


def test_enforce_does_not_raise_on_warnings():
    enforce([CheckResult("w", "sales", "warn", "", "")])
