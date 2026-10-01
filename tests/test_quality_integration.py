"""Integration: quality gates and parity against the real loaded warehouse (run the DAG first)."""

from __future__ import annotations

import hashlib

import psycopg
import pytest

from sales_pipeline.parity import ParityError, run_parity
from sales_pipeline.quality import DataQualityError, validate_source
from sales_pipeline.sources import SOURCES

pytestmark = pytest.mark.integration


@pytest.fixture
def conn(warehouse_env):
    with psycopg.connect(warehouse_env.conninfo()) as c:
        yield c
        c.rollback()
        c.execute("DELETE FROM ops.dq_results WHERE batch_id LIKE 'it-%'")
        c.execute("DELETE FROM ops.parity_report WHERE batch_id LIKE 'it-%'")
        c.commit()


def test_parity_on_real_sources_reports_known_gaps(conn, tmp_path):
    results = {r["check"]: r for r in run_parity(conn, "it-parity", tmp_path)}
    assert results["store_reference_vs_state_mapping"]["status"] == "pass"
    assert results["sales_vs_store_reference"]["status"] == "pass"
    coverage = results["sales_calendar_coverage"]
    assert coverage["status"] == "known"
    assert coverage["discrepancies"] == 180 * 184 + 1  # H2-2014 gap + store 988 on 2013-01-01
    assert (tmp_path / "parity_it-parity.json").exists()


def test_parity_fails_on_new_gap_and_report_survives(conn, warehouse_env, tmp_path):
    # Remove one real store-day (restored in `finally`): a gap no allowlist rule explains.
    where = "store_id = 1 AND sales_date = '2015-03-02'"
    conn.execute(f"CREATE TEMP TABLE saved AS SELECT * FROM raw.sales WHERE {where}")
    conn.execute(f"DELETE FROM raw.sales WHERE {where}")
    try:
        with pytest.raises(ParityError, match="not covered by any rule"):
            run_parity(conn, "it-parity-fail", tmp_path)
    finally:
        conn.execute("INSERT INTO raw.sales SELECT * FROM saved")
        conn.commit()
    assert conn.execute(f"SELECT count(*) FROM raw.sales WHERE {where}").fetchone() == (1,)
    with psycopg.connect(warehouse_env.conninfo()) as other:  # what on-call would actually see
        assert other.execute(
            "SELECT status FROM ops.parity_report WHERE batch_id = 'it-parity-fail' "
            "AND check_name = 'sales_calendar_coverage'"
        ).fetchone() == ("fail",)


def test_tampered_file_fails_validation_and_is_recorded(conn, warehouse_env, tmp_path):
    bad = tmp_path / "stores.bin"
    bad.write_text("Store,StoreType,Assortment,NewColumn\n1,c,a,x\n")
    with pytest.raises(DataQualityError) as exc:
        validate_source(conn, "stores", bad, batch_id="it-tampered")
    checks = {f.check for f in exc.value.failures}
    assert {"schema_drift", "row_volume_bounds"} <= checks
    # Failures are committed *before* raising. Checked from a separate connection: an earlier version
    # only "passed" because the same connection could see its own uncommitted rows.
    conn.rollback()
    with psycopg.connect(warehouse_env.conninfo()) as other:
        rows = other.execute(
            "SELECT check_name FROM ops.dq_results WHERE batch_id = 'it-tampered' AND status = 'fail'"
        ).fetchall()
    assert {r[0] for r in rows} >= {"schema_drift", "row_volume_bounds"}


def test_real_landed_files_pass_validation(conn):
    from sales_pipeline.config import data_dir

    for name, src in SOURCES.items():
        path = data_dir() / "landing" / f"{name}.bin"
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != src.sha256:
            pytest.skip("run the DAG first so landed files exist")
        statuses = {r["check"]: r["status"] for r in validate_source(conn, name, path, batch_id="it-real")}
        assert statuses["schema_drift"] == statuses["type_drift"] == statuses["row_volume_bounds"] == "pass"
