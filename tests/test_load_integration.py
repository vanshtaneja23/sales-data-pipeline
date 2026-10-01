"""Integration: load into the real compose Postgres (make up && make test-integration)."""

from __future__ import annotations

import psycopg
import pytest
from conftest import make_stores

from sales_pipeline.clean import clean_stores
from sales_pipeline.contracts import Contract
from sales_pipeline.load import WarehouseSchemaError, load_frame

pytestmark = pytest.mark.integration


@pytest.fixture
def conn(warehouse_env):
    with psycopg.connect(warehouse_env.conninfo()) as c:
        yield c
        c.rollback()
        c.execute("DROP SCHEMA IF EXISTS it_test CASCADE")
        c.execute("DELETE FROM ops.load_audit WHERE batch_id LIKE 'it-%'")
        c.commit()


@pytest.fixture
def contract(stores_contract) -> Contract:
    # Same contract, isolated schema so the test never touches real raw tables.
    return Contract(**{**stores_contract.__dict__, "table": "it_test.stores"})


def test_load_is_idempotent_and_audited(conn, contract):
    result = clean_stores(make_stores([{"Store": "1"}, {"Store": "2"}]), contract)
    load_frame(conn, contract, result, batch_id="it-1", sha256="x" * 64)
    summary = load_frame(conn, contract, result, batch_id="it-2", sha256="x" * 64)

    assert summary.rows_loaded == 2
    rows = conn.execute("SELECT count(*), count(DISTINCT _batch_id) FROM it_test.stores").fetchone()
    assert rows == (2, 1)  # second load replaced the first, not appended
    audit = conn.execute(
        "SELECT rows_loaded FROM ops.load_audit WHERE batch_id = 'it-2' AND source = 'stores'"
    ).fetchone()
    assert audit == (2,)


def test_nulls_round_trip_as_sql_null(conn, contract):
    result = clean_stores(make_stores([{"CompetitionDistance": ""}]), contract)
    load_frame(conn, contract, result, batch_id="it-3", sha256="x" * 64)
    assert conn.execute("SELECT competition_distance_m FROM it_test.stores").fetchone() == (None,)


def test_table_drift_in_warehouse_fails_loudly(conn, contract):
    result = clean_stores(make_stores(), contract)
    load_frame(conn, contract, result, batch_id="it-4", sha256="x" * 64)
    conn.execute("ALTER TABLE it_test.stores ADD COLUMN surprise int")
    conn.commit()
    with pytest.raises(WarehouseSchemaError, match="surprise"):
        load_frame(conn, contract, result, batch_id="it-5", sha256="x" * 64)
