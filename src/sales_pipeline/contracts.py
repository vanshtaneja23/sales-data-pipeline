"""Data contracts: one YAML per source declares columns, types, bounds and volume expectations.

The same contract drives the target DDL, the cleaning casts and the quality gates, so the
three can never disagree about what a column is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sales_pipeline.config import contracts_dir

PG_TYPES = {
    "integer": "integer",
    "smallint": "smallint",
    "numeric": "numeric",
    "text": "text",
    "date": "date",
    "boolean": "boolean",
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool
    source: str | None = None
    allowed: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class Contract:
    source: str
    table: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    derived_columns: tuple[Column, ...] = ()
    volume: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] | None = None

    @property
    def source_columns(self) -> list[str]:
        return [c.source for c in self.columns if c.source]

    @property
    def rename_map(self) -> dict[str, str]:
        return {c.source: c.name for c in self.columns if c.source}

    @property
    def all_columns(self) -> tuple[Column, ...]:
        return self.columns + self.derived_columns

    @property
    def schema_name(self) -> str:
        return self.table.split(".")[0]

    def create_table_sql(self) -> str:
        cols = [
            f"{c.name} {PG_TYPES[c.type]}{'' if c.nullable else ' NOT NULL'}"
            for c in self.all_columns
        ]
        cols += ["_batch_id text NOT NULL", "_loaded_at timestamptz NOT NULL DEFAULT now()"]
        pk = f", PRIMARY KEY ({', '.join(self.primary_key)})" if self.primary_key else ""
        return f"CREATE TABLE IF NOT EXISTS {self.table} (\n  " + ",\n  ".join(cols) + pk + "\n)"


def _column(raw: dict[str, Any]) -> Column:
    allowed = raw.get("allowed")
    return Column(
        name=raw["name"],
        type=raw["type"],
        nullable=bool(raw.get("nullable", False)),
        source=raw.get("source"),
        allowed=tuple(str(a) for a in allowed) if allowed else None,
        min=raw.get("min"),
        max=raw.get("max"),
    )


def load_contract(source: str, directory: Path | None = None) -> Contract:
    path = (directory or contracts_dir()) / f"{source}.yml"
    raw = yaml.safe_load(path.read_text())
    unknown = {c["type"] for c in raw["columns"] + raw.get("derived_columns", [])} - set(PG_TYPES)
    if unknown:
        raise ValueError(f"{path}: unsupported column types {sorted(unknown)}")
    return Contract(
        source=raw["source"],
        table=raw["table"],
        primary_key=tuple(raw.get("primary_key", [])),
        columns=tuple(_column(c) for c in raw["columns"]),
        derived_columns=tuple(_column(c) for c in raw.get("derived_columns", [])),
        volume=raw.get("volume", {}),
        freshness=raw.get("freshness"),
    )
