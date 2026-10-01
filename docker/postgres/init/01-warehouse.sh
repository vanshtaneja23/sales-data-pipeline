#!/bin/bash
# Runs once, on first container start with an empty volume.
# The default DB (POSTGRES_DB) holds Airflow metadata; the warehouse is separate
# so dbt/analytics users never touch Airflow's tables.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE DATABASE ${WAREHOUSE_DB};
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$WAREHOUSE_DB" <<-EOSQL
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE SCHEMA IF NOT EXISTS raw;
  CREATE SCHEMA IF NOT EXISTS ops;
  CREATE SCHEMA IF NOT EXISTS rag;
EOSQL
