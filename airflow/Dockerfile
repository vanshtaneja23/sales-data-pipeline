FROM apache/airflow:3.3.2-python3.12

# dbt gets its own virtualenv: dbt-core and Airflow pin overlapping libraries
# (protobuf, jinja, etc.) differently, and sharing one env breaks one of them.
USER root
RUN python -m venv /opt/dbt-venv \
 && /opt/dbt-venv/bin/pip install --no-cache-dir "dbt-core==1.12.5" "dbt-postgres==1.11.0" \
 && chown -R airflow:0 /opt/dbt-venv
USER airflow

# pandas 3.0.5, pyarrow and psycopg 3 already ship with this image; only add test tooling.
RUN pip install --no-cache-dir "apache-airflow==3.3.2" "pytest==8.4.2"

ENV DBT_BIN=/opt/dbt-venv/bin/dbt
