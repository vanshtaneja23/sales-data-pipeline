from __future__ import annotations

import pytest
from conftest import CONTRACTS

from sales_pipeline.contracts import load_contract
from sales_pipeline.sources import SOURCES

# Headers exactly as they appear in the pinned upstream files.
UPSTREAM_HEADERS = {
    "sales": ["Store", "DayOfWeek", "Date", "Sales", "Customers", "Open", "Promo",
              "StateHoliday", "SchoolHoliday"],
    "stores": ["Store", "StoreType", "Assortment", "CompetitionDistance",
               "CompetitionOpenSinceMonth", "CompetitionOpenSinceYear", "Promo2",
               "Promo2SinceWeek", "Promo2SinceYear", "PromoInterval"],
    "store_states": ["Store", "State"],
}


@pytest.mark.parametrize("name", list(SOURCES))
def test_every_source_has_a_contract_matching_upstream(name):
    contract = load_contract(name, CONTRACTS)
    assert contract.source_columns == UPSTREAM_HEADERS[name]
    assert contract.volume["min_rows"] < contract.volume["max_rows"]


def test_ddl_has_not_null_pk_and_lineage(sales_contract):
    ddl = sales_contract.create_table_sql()
    assert "CREATE TABLE IF NOT EXISTS raw.sales" in ddl
    assert "sales_amount integer NOT NULL" in ddl
    assert "PRIMARY KEY (store_id, sales_date)" in ddl
    assert "_loaded_at timestamptz NOT NULL DEFAULT now()" in ddl


def test_nullable_column_has_no_not_null(stores_contract):
    assert "competition_distance_m numeric," in stores_contract.create_table_sql()
