"""Fast guard: every dbt YAML file must parse. A syntax slip here otherwise only surfaces mid-DAG."""

from __future__ import annotations

import pytest
import yaml

from sales_pipeline.config import REPO_ROOT

DBT_DIR = REPO_ROOT / "dbt"
# Source YAML only: dbt/target holds generated artefacts (including *directories* named like _marts.yml).
DBT_YAML = sorted(
    p for p in DBT_DIR.rglob("*.yml") if p.is_file() and "target" not in p.relative_to(DBT_DIR).parts
)


@pytest.mark.parametrize("path", DBT_YAML, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_dbt_yaml_parses(path):
    assert yaml.safe_load(path.read_text()) is not None


def test_every_mart_column_is_documented():
    doc = yaml.safe_load((REPO_ROOT / "dbt/models/marts/_marts.yml").read_text())
    for model in doc["models"]:
        assert model.get("description"), model["name"]
        for col in model.get("columns", []):
            assert col.get("description"), f"{model['name']}.{col['name']} has no description"
