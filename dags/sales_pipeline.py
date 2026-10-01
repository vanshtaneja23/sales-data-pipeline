"""Rossmann sales batch pipeline.

    extract_<src>  download + SHA-256 verify            (retries: transient network errors)
    validate_<src> schema drift, type drift, row volume (gate: before anything is loaded)
    load_<src>     pandas clean + transactional COPY into raw.*
    check_freshness / dbt_source_freshness / parity_check   (gates: after load, before marts)
    dbt_build      staging -> marts, with dbt tests run per model

Quality gates never retry: a data problem doesn't fix itself in 30 seconds, and retrying only
delays the alert. Any failure fails the run, skips everything downstream (marts keep serving
the last good build) and fires `on_task_failure` (structured ERROR log + ops.pipeline_alerts).

Business logic lives in src/sales_pipeline; this file only wires tasks together.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Param, dag, get_current_context, task

from sales_pipeline.alerts import on_task_failure
from sales_pipeline.sources import SOURCES

DBT_CMD = (
    "cd $DBT_PROJECT_DIR && $DBT_BIN {cmd} --project-dir $DBT_PROJECT_DIR "
    "--profiles-dir $DBT_PROFILES_DIR"
)


def _warehouse():
    import psycopg

    from sales_pipeline.config import WarehouseConfig

    # autocommit: each `conn.transaction()` block commits on its own, so a gate's recorded results
    # survive the exception it raises afterwards.
    return psycopg.connect(WarehouseConfig.from_env().conninfo(), autocommit=True)


@dag(
    dag_id="sales_pipeline",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-eng", "retries": 0, "on_failure_callback": on_task_failure},
    params={
        # The source is a frozen extract ending 2015-07-31. In production this would be the
        # run's logical date; here it is a param so freshness is meaningful and testable.
        "as_of_date": Param("2015-07-31", type="string", format="date"),
    },
    tags=["sales", "dbt", "data-quality"],
    doc_md=__doc__,
)
def sales_pipeline():
    @task(retries=2, retry_delay=timedelta(seconds=30))
    def extract(source_name: str) -> str:
        from sales_pipeline.config import data_dir
        from sales_pipeline.ingest import download

        return str(download(SOURCES[source_name], data_dir() / "landing"))

    @task
    def validate(source_name: str, path: str) -> list[dict]:
        from sales_pipeline.quality import validate_source

        with _warehouse() as conn:
            return validate_source(conn, source_name, Path(path), batch_id=get_current_context()["run_id"])

    @task
    def load(source_name: str, path: str) -> dict:
        from sales_pipeline.load import load_source

        with _warehouse() as conn:
            run_id = get_current_context()["run_id"]
            return asdict(load_source(conn, source_name, Path(path), batch_id=run_id))

    @task
    def check_freshness() -> list[dict]:
        from sales_pipeline.quality import validate_freshness

        ctx = get_current_context()
        as_of = date.fromisoformat(ctx["params"]["as_of_date"])
        with _warehouse() as conn:
            return validate_freshness(conn, as_of, batch_id=ctx["run_id"])

    @task
    def parity_check() -> list[dict]:
        from sales_pipeline.config import data_dir
        from sales_pipeline.parity import run_parity

        with _warehouse() as conn:
            return run_parity(conn, get_current_context()["run_id"], data_dir() / "reports")

    dbt_source_freshness = BashOperator(
        task_id="dbt_source_freshness", bash_command=DBT_CMD.format(cmd="source freshness")
    )
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=DBT_CMD.format(cmd="build") + " && " + DBT_CMD.format(cmd="docs generate"),
    )

    loads = []
    for name in SOURCES:
        path = extract.override(task_id=f"extract_{name}")(name)
        gate = validate.override(task_id=f"validate_{name}")(name, path)
        loaded = load.override(task_id=f"load_{name}")(name, path)
        gate >> loaded
        loads.append(loaded)

    post_load_gates = [check_freshness(), dbt_source_freshness, parity_check()]
    for gate in post_load_gates:
        loads >> gate
        gate >> dbt_build


sales_pipeline()
