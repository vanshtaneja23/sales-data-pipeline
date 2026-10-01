"""DAG structure tests. Run inside the Airflow container: `make test-dag`."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.airflow


@pytest.fixture(scope="module")
def dag():
    from airflow.models import DagBag

    bag = DagBag(dag_folder="/opt/airflow/dags")  # examples disabled via AIRFLOW__CORE__LOAD_EXAMPLES
    assert not bag.import_errors, bag.import_errors
    return bag.get_dag("sales_pipeline")


def test_dag_loads(dag):
    assert dag is not None


def test_every_source_extracted_then_loaded(dag):
    from sales_pipeline.sources import SOURCES

    for name in SOURCES:
        assert f"load_{name}" in dag.get_task(f"extract_{name}").downstream_task_ids


def test_dbt_waits_for_all_loads(dag):
    from sales_pipeline.sources import SOURCES

    assert {f"load_{n}" for n in SOURCES} <= _all_upstream(dag, "dbt_build")


def _all_upstream(dag, task_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [task_id]
    while stack:
        for up in dag.get_task(stack.pop()).upstream_task_ids:
            if up not in seen:
                seen.add(up)
                stack.append(up)
    return seen
