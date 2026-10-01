"""Failure alerting for the DAG.

Airflow already marks the task and run failed; this callback makes the failure *loud* outside
the UI: a structured ERROR log line (shippable to any log alerting) and a row in
ops.pipeline_alerts that dashboards or on-call tooling can poll.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("sales_pipeline.alerts")


def build_alert(context: dict[str, Any]) -> dict[str, str]:
    ti = context.get("task_instance") or context.get("ti")
    error = context.get("exception")
    return {
        "event": "pipeline_alert",
        "dag_id": getattr(ti, "dag_id", "unknown"),
        "task_id": getattr(ti, "task_id", "unknown"),
        "run_id": str(context.get("run_id", "unknown")),
        "error": (f"{type(error).__name__}: {error}" if error else "unknown error")[:4000],
    }


def on_task_failure(context: dict[str, Any]) -> None:
    alert = build_alert(context)
    log.error("PIPELINE_ALERT %s", json.dumps(alert))
    # The alert sink must never mask the original failure, but its own failure is logged, not hidden.
    try:
        import psycopg

        from sales_pipeline.config import WarehouseConfig
        from sales_pipeline.ops import ensure_ops_tables

        with psycopg.connect(WarehouseConfig.from_env().conninfo()) as conn:
            ensure_ops_tables(conn)
            conn.execute(
                "INSERT INTO ops.pipeline_alerts (dag_id, task_id, run_id, error) VALUES (%s, %s, %s, %s)",
                (alert["dag_id"], alert["task_id"], alert["run_id"], alert["error"]),
            )
            conn.commit()
    except Exception:
        log.exception("Could not persist pipeline alert to ops.pipeline_alerts")
