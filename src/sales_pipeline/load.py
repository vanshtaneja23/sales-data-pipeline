"""Load cleaned frames into Postgres and record an audit row per source per batch.

Each load is one transaction: create-if-missing, TRUNCATE, COPY, verify count, write audit.
Readers see either the previous complete snapshot or the new one, never a half-loaded table,
and re-running a batch is idempotent.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from sales_pipeline.clean import CLEANERS, CleanResult
from sales_pipeline.contracts import Contract, load_contract
from sales_pipeline.ingest import read_raw, sha256_of
from sales_pipeline.ops import ensure_ops_tables
from sales_pipeline.sources import SOURCES

log = logging.getLogger(__name__)



class WarehouseSchemaError(RuntimeError):
    """The existing warehouse table no longer matches the contract."""


@dataclass(frozen=True)
class LoadSummary:
    source: str
    table: str
    rows_in: int
    rows_loaded: int
    issues: dict[str, int]


def _table_ident(contract: Contract) -> sql.Identifier:
    schema, table = contract.table.split(".")
    return sql.Identifier(schema, table)


def _assert_table_matches(cur: psycopg.Cursor, contract: Contract) -> None:
    schema, table = contract.table.split(".")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    )
    actual = [r[0] for r in cur.fetchall()]
    expected = [c.name for c in contract.all_columns] + ["_batch_id", "_loaded_at"]
    if actual != expected:
        raise WarehouseSchemaError(
            f"{contract.table} columns {actual} != contract {expected}. "
            "Contract changed: migrate the table deliberately (dbt views depend on it)."
        )


def load_frame(
    conn: psycopg.Connection, contract: Contract, result: CleanResult, batch_id: str, sha256: str
) -> LoadSummary:
    df = result.frame
    columns = [c.name for c in contract.all_columns]
    ident = _table_ident(contract)

    buf = io.StringIO()
    df[columns].assign(_batch_id=batch_id).to_csv(
        buf, index=False, header=False, date_format="%Y-%m-%d"
    )

    ensure_ops_tables(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(contract.schema_name))
        )
        cur.execute(contract.create_table_sql())
        _assert_table_matches(cur, contract)
        cur.execute(sql.SQL("TRUNCATE {}").format(ident))
        # _loaded_at defaults to now(), which is the transaction start: one timestamp per snapshot.
        copy_cols = sql.SQL(", ").join(map(sql.Identifier, [*columns, "_batch_id"]))
        with cur.copy(
            sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv)").format(ident, copy_cols)
        ) as copy:
            copy.write(buf.getvalue())
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
        loaded = cur.fetchone()[0]
        if loaded != len(df):
            raise RuntimeError(f"{contract.table}: COPY loaded {loaded} rows, expected {len(df)}")
        cur.execute(
            """
            INSERT INTO ops.load_audit (batch_id, source, source_sha256, rows_in, rows_loaded, issues)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id, source) DO UPDATE SET
              source_sha256 = EXCLUDED.source_sha256, rows_in = EXCLUDED.rows_in,
              rows_loaded = EXCLUDED.rows_loaded, issues = EXCLUDED.issues, loaded_at = now()
            """,
            (batch_id, contract.source, sha256, result.rows_in, loaded, json.dumps(result.issues)),
        )

    summary = LoadSummary(contract.source, contract.table, result.rows_in, loaded, result.issues)
    log.info("load_complete %s", json.dumps(asdict(summary)))
    return summary


def load_source(
    conn: psycopg.Connection, source_name: str, path: Path, batch_id: str
) -> LoadSummary:
    source = SOURCES[source_name]
    contract = load_contract(source_name)
    result = CLEANERS[source_name](read_raw(source, path), contract)
    return load_frame(conn, contract, result, batch_id, sha256_of(path))
