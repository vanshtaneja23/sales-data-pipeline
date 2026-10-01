"""Rossmann sales batch pipeline.

extract (download + checksum) -> load (pandas clean + COPY into raw.*) -> dbt build (staging -> marts + tests)

Business logic lives in src/sales_pipeline; this file only wires tasks together so the logic is
unit-testable without Airflow.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, get_current_context, task

from sales_pipeline.sources import SOURCES

DBT_CMD = (
    "cd $DBT_PROJECT_DIR && $DBT_BIN {cmd} --project-dir $DBT_PROJECT_DIR "
    "--profiles-dir $DBT_PROFILES_DIR"
)


@dag(
    dag_id="sales_pipeline",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-eng", "retries": 0},
    params={"as_of_date": "2015-07-31"},
    tags=["sales", "dbt"],
    doc_md=__doc__,
)
def sales_pipeline():
    @task(retries=2, retry_delay=timedelta(seconds=30))
    def extract(source_name: str) -> str:
        from sales_pipeline.config import data_dir
        from sales_pipeline.ingest import download

        return str(download(SOURCES[source_name], data_dir() / "landing"))

    @task
    def load(source_name: str, path: str) -> dict:
        import psycopg

        from sales_pipeline.config import WarehouseConfig
        from sales_pipeline.load import load_source

        run_id = get_current_context()["run_id"]
        with psycopg.connect(WarehouseConfig.from_env().conninfo()) as conn:
            return asdict(load_source(conn, source_name, Path(path), batch_id=run_id))

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=DBT_CMD.format(cmd="build") + " && " + DBT_CMD.format(cmd="docs generate"),
    )

    for name in SOURCES:
        path = extract.override(task_id=f"extract_{name}")(name)
        load.override(task_id=f"load_{name}")(name, path) >> dbt_build


sales_pipeline()
