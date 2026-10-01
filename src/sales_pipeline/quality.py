"""Data-quality gates: schema drift, type drift, row volume and freshness.

Every check returns a CheckResult. Results are *always* persisted to ops.dq_results first and
then `enforce()` raises DataQualityError if any failed, so a failing run leaves a queryable
record of exactly what broke and the Airflow task (and therefore the DAG) fails.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import psycopg

from sales_pipeline.clean import NORMALISERS, type_errors
from sales_pipeline.contracts import Contract, load_contract
from sales_pipeline.ingest import read_raw
from sales_pipeline.ops import ensure_ops_tables
from sales_pipeline.sources import SOURCES

log = logging.getLogger(__name__)

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    check: str
    source: str
    status: Status
    observed: str
    expected: str
    detail: str = ""


class DataQualityError(RuntimeError):
    def __init__(self, failures: list[CheckResult]):
        self.failures = failures
        lines = [f"  - [{f.source}] {f.check}: observed {f.observed}, expected {f.expected}. {f.detail}"
                 for f in failures]
        super().__init__(f"{len(failures)} data quality check(s) failed:\n" + "\n".join(lines))


# --- individual checks (pure functions, unit-tested) -----------------------------------------

def check_schema_drift(header: list[str], contract: Contract) -> CheckResult:
    expected = contract.source_columns
    missing = [c for c in expected if c not in header]
    added = [c for c in header if c not in expected]
    if missing or added:
        return CheckResult(
            "schema_drift", contract.source, "fail",
            observed=f"missing={missing} added={added}", expected=str(expected),
            detail="Upstream columns changed. Update the contract deliberately before loading.",
        )
    reordered = header != expected
    return CheckResult(
        "schema_drift", contract.source, "pass", observed=str(header), expected=str(expected),
        detail="column order differs (harmless: columns are mapped by name)" if reordered else "",
    )


def check_type_drift(raw: pd.DataFrame, contract: Contract) -> CheckResult:
    normalised = NORMALISERS[contract.source](raw)
    errors = type_errors(normalised, contract)
    if errors:
        return CheckResult(
            "type_drift", contract.source, "fail",
            observed=f"{len(errors)} column(s) violate contract", expected="all columns castable",
            detail=" | ".join(errors.values()),
        )
    return CheckResult("type_drift", contract.source, "pass",
                       observed="all columns castable", expected="all columns castable")


def check_volume(rows: int, contract: Contract, previous_rows: int | None) -> list[CheckResult]:
    vol = contract.volume
    lo, hi = vol["min_rows"], vol["max_rows"]
    results = [CheckResult(
        "row_volume_bounds", contract.source, "pass" if lo <= rows <= hi else "fail",
        observed=str(rows), expected=f"[{lo}, {hi}]",
        detail="" if lo <= rows <= hi else "Row count outside contract bounds (truncation or duplication?)",
    )]
    if previous_rows:
        change = abs(rows - previous_rows) / previous_rows * 100
        limit = vol["max_change_pct"]
        results.append(CheckResult(
            "row_volume_change", contract.source, "pass" if change <= limit else "fail",
            observed=f"{change:.2f}% vs previous load ({previous_rows} rows)", expected=f"<= {limit}%",
        ))
    else:
        results.append(CheckResult(
            "row_volume_change", contract.source, "warn", observed="no previous successful load",
            expected=f"<= {vol['max_change_pct']}%", detail="First load: no baseline to compare against.",
        ))
    return results


def check_freshness(source: str, max_date: date | None, as_of: date, max_lag_days: int) -> CheckResult:
    if max_date is None:
        return CheckResult(
            "freshness", source, "fail", observed="no rows", expected=f"lag <= {max_lag_days}d"
        )
    lag = (as_of - max_date).days
    if lag < 0:
        return CheckResult("freshness", source, "fail", observed=f"latest date {max_date}",
                           expected=f"<= as_of {as_of}", detail="Data dated in the future relative to as_of.")
    return CheckResult(
        "freshness", source, "pass" if lag <= max_lag_days else "fail",
        observed=f"latest date {max_date} ({lag}d behind as_of {as_of})", expected=f"lag <= {max_lag_days}d",
        detail="" if lag <= max_lag_days else "Source is stale: upstream did not deliver recent data.",
    )


# --- persistence and enforcement -------------------------------------------------------------

def record_results(conn: psycopg.Connection, batch_id: str, results: list[CheckResult]) -> None:
    ensure_ops_tables(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ops.dq_results (batch_id, check_name, source, status, observed, expected, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(batch_id, r.check, r.source, r.status, r.observed, r.expected, r.detail) for r in results],
        )
    # Commit now: enforce() raises right after this, and a connection context manager rolls back
    # on exception -- which would silently erase the record of *why* the run failed.
    if not conn.autocommit:
        conn.commit()
    for r in results:
        level = {"pass": logging.INFO, "warn": logging.WARNING, "fail": logging.ERROR}[r.status]
        log.log(level, "dq_check %s", json.dumps({"batch_id": batch_id, **asdict(r)}))


def enforce(results: list[CheckResult]) -> None:
    failures = [r for r in results if r.status == "fail"]
    if failures:
        raise DataQualityError(failures)


def previous_row_count(conn: psycopg.Connection, source: str, batch_id: str) -> int | None:
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT rows_loaded FROM ops.load_audit WHERE source = %s AND batch_id <> %s "
        "ORDER BY loaded_at DESC LIMIT 1",
        (source, batch_id),
    ).fetchone()
    return row[0] if row else None


# --- task entry points -------------------------------------------------------------------------

def validate_source(conn: psycopg.Connection, source_name: str, path: Path, batch_id: str) -> list[dict]:
    """Pre-load gate on the landed file: schema drift, type drift, volume."""
    contract = load_contract(source_name)
    raw = read_raw(SOURCES[source_name], path)
    results = [check_schema_drift(list(raw.columns), contract)]
    if results[0].status == "pass":  # type checks are meaningless if columns are missing
        results.append(check_type_drift(raw, contract))
    results += check_volume(len(raw), contract, previous_row_count(conn, source_name, batch_id))
    record_results(conn, batch_id, results)
    enforce(results)
    return [asdict(r) for r in results]


def validate_freshness(conn: psycopg.Connection, as_of: date, batch_id: str) -> list[dict]:
    """Post-load gate: every contract with a freshness rule must be within its lag."""
    results = []
    for name in SOURCES:
        contract = load_contract(name)
        if not contract.freshness:
            continue
        col = contract.freshness["date_column"]
        max_date = conn.execute(f"SELECT max({col}) FROM {contract.table}").fetchone()[0]  # noqa: S608 (contract-defined identifiers)
        results.append(check_freshness(name, max_date, as_of, contract.freshness["max_lag_days"]))
    record_results(conn, batch_id, results)
    enforce(results)
    return [asdict(r) for r in results]
