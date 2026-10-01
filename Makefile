SHELL := /bin/bash
COMPOSE := docker compose
AIRFLOW := $(COMPOSE) exec -T airflow-scheduler
# PYTHONPATH=src: macOS can flag venv .pth files "hidden", which Python 3.12 then skips.
PY := PYTHONPATH=src .venv/bin/python

.PHONY: help env up down nuke run trigger test test-integration test-dag test-all dbt-docs lint \
        drill-stale notebook rag-index ask eval

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

env: ## create .env with random local secrets (never committed)
	@test -f .env && echo ".env exists, leaving it alone" || ( \
	  cp .env.example .env && \
	  sed -i '' "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$$(openssl rand -hex 16)|" .env && \
	  sed -i '' "s|^AIRFLOW_JWT_SECRET=.*|AIRFLOW_JWT_SECRET=$$(openssl rand -hex 32)|" .env && \
	  sed -i '' "s|^AIRFLOW_UID=.*|AIRFLOW_UID=$$(id -u)|" .env && echo "wrote .env" )

up: ## build images and start Postgres + Airflow
	$(COMPOSE) build
	$(COMPOSE) up -d --wait

down: ## stop the stack (keeps data volume)
	$(COMPOSE) down

nuke: ## stop the stack and delete the Postgres volume
	$(COMPOSE) down -v

run: ## run the whole DAG once in-process (airflow dags test), streaming logs
	$(AIRFLOW) airflow dags test sales_pipeline

trigger: ## trigger the DAG through the scheduler (watch it at http://localhost:8080)
	$(AIRFLOW) airflow dags unpause sales_pipeline
	$(AIRFLOW) airflow dags trigger sales_pipeline

test: ## unit tests (no services needed)
	$(PY) -m pytest -q

test-integration: ## tests that hit the compose Postgres
	set -a && source .env && set +a && WAREHOUSE_PORT=$${WAREHOUSE_HOST_PORT:-5433} \
	  WAREHOUSE_USER=$$POSTGRES_USER WAREHOUSE_PASSWORD=$$POSTGRES_PASSWORD \
	  $(PY) -m pytest -q -m integration

test-dag: ## DAG integrity tests inside the Airflow container
	$(AIRFLOW) python -m pytest -q -p no:cacheprovider -m airflow /opt/airflow/tests/test_dag_integrity.py

test-all: test test-integration test-dag

dbt-docs: ## regenerate dbt docs (manifest/catalog) inside the container
	$(AIRFLOW) bash -c 'cd $$DBT_PROJECT_DIR && $$DBT_BIN docs generate'

lint:
	.venv/bin/ruff check src dags tests

drill-stale: ## failure drill: run via the scheduler with as_of 61 days after the data ends -> must fail
	$(AIRFLOW) airflow dags unpause sales_pipeline
	$(AIRFLOW) airflow dags trigger sales_pipeline --run-id drill_stale_$$(date +%s) --conf '{"as_of_date": "2015-09-30"}'

notebook: ## build + execute the store-tiering notebook against the warehouse (outputs committed)
	$(PY) analytics/build_notebook.py
	set -a && source .env && set +a && WAREHOUSE_PORT=$${WAREHOUSE_HOST_PORT:-5433} \
	  WAREHOUSE_USER=$$POSTGRES_USER WAREHOUSE_PASSWORD=$$POSTGRES_PASSWORD \
	  .venv/bin/jupyter nbconvert --to notebook --execute --inplace \
	  --ExecutePreprocessor.timeout=600 analytics/store_tiering.ipynb

WAREHOUSE_ENV = set -a && source .env && set +a && WAREHOUSE_PORT=$${WAREHOUSE_HOST_PORT:-5433} \
	  WAREHOUSE_USER=$$POSTGRES_USER WAREHOUSE_PASSWORD=$$POSTGRES_PASSWORD

rag-index: ## (re)index dbt docs + data dictionary into pgvector (incremental by content hash)
	$(WAREHOUSE_ENV) $(PY) -m sales_rag.cli index

ask: ## ask the docs assistant: make ask Q="what does sales_per_customer mean?"
	$(WAREHOUSE_ENV) $(PY) -m sales_rag.cli ask "$(Q)"

eval: ## run the RAG evaluation harness (writes eval/results/)
	$(WAREHOUSE_ENV) $(PY) -m sales_rag.cli eval
