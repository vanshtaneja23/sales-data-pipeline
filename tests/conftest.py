from __future__ import annotations

import os

import pandas as pd
import pytest

from sales_pipeline.config import REPO_ROOT
from sales_pipeline.contracts import load_contract

CONTRACTS = REPO_ROOT / "contracts"


@pytest.fixture
def sales_contract():
    return load_contract("sales", CONTRACTS)


@pytest.fixture
def stores_contract():
    return load_contract("stores", CONTRACTS)


@pytest.fixture
def states_contract():
    return load_contract("store_states", CONTRACTS)


def make_sales(rows: list[dict] | None = None) -> pd.DataFrame:
    """All-string frame shaped like train.csv. 2015-07-31 was a Friday (ISO weekday 5)."""
    base = {
        "Store": "1", "DayOfWeek": "5", "Date": "2015-07-31", "Sales": "5263", "Customers": "555",
        "Open": "1", "Promo": "1", "StateHoliday": "0", "SchoolHoliday": "1",
    }
    rows = rows or [{}]
    return pd.DataFrame([{**base, **r} for r in rows], dtype=str)


def make_stores(rows: list[dict] | None = None) -> pd.DataFrame:
    base = {
        "Store": "1", "StoreType": "c", "Assortment": "a", "CompetitionDistance": "1270",
        "CompetitionOpenSinceMonth": "9", "CompetitionOpenSinceYear": "2008", "Promo2": "0",
        "Promo2SinceWeek": "", "Promo2SinceYear": "", "PromoInterval": "",
    }
    rows = rows or [{}]
    return pd.DataFrame([{**base, **r} for r in rows], dtype=str)


@pytest.fixture
def warehouse_env():
    """Connection settings for integration tests; skips if the stack isn't configured."""
    if not os.environ.get("WAREHOUSE_PASSWORD"):
        pytest.skip("WAREHOUSE_* env not set; run via `make test-integration`")
    from sales_pipeline.config import WarehouseConfig

    return WarehouseConfig.from_env()
