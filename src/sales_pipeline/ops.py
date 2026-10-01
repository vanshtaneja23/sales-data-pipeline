"""DDL for operational tables in schema `ops` (audit, quality results, parity, alerts)."""

from __future__ import annotations

import psycopg

OPS_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.load_audit (
  batch_id      text        NOT NULL,
  source        text        NOT NULL,
  source_sha256 text        NOT NULL,
  rows_in       integer     NOT NULL,
  rows_loaded   integer     NOT NULL,
  issues        jsonb       NOT NULL,
  loaded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, source)
);

CREATE TABLE IF NOT EXISTS ops.dq_results (
  id          bigserial   PRIMARY KEY,
  batch_id    text        NOT NULL,
  check_name  text        NOT NULL,
  source      text        NOT NULL,
  status      text        NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
  observed    text,
  expected    text,
  detail      text,
  checked_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.parity_report (
  id             bigserial   PRIMARY KEY,
  batch_id       text        NOT NULL,
  check_name     text        NOT NULL,
  left_source    text        NOT NULL,
  right_source   text        NOT NULL,
  status         text        NOT NULL CHECK (status IN ('pass', 'known', 'fail')),
  discrepancies  integer     NOT NULL,
  sample         jsonb       NOT NULL,
  detail         text,
  checked_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.pipeline_alerts (
  id          bigserial   PRIMARY KEY,
  dag_id      text        NOT NULL,
  task_id     text        NOT NULL,
  run_id      text        NOT NULL,
  error       text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
"""


def ensure_ops_tables(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(OPS_DDL)
