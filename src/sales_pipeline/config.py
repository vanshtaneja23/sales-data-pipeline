"""Runtime configuration, read from environment variables (populated from .env by compose)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WarehouseConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> WarehouseConfig:
        missing = [k for k in ("WAREHOUSE_USER", "WAREHOUSE_PASSWORD") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            host=os.environ.get("WAREHOUSE_HOST", "localhost"),
            port=int(os.environ.get("WAREHOUSE_PORT", "5433")),
            dbname=os.environ.get("WAREHOUSE_DB", "warehouse"),
            user=os.environ["WAREHOUSE_USER"],
            password=os.environ["WAREHOUSE_PASSWORD"],
        )

    def conninfo(self) -> str:
        # psycopg accepts keyword conninfo; password is never logged because we never log this string.
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", REPO_ROOT / "data"))


def contracts_dir() -> Path:
    return Path(os.environ.get("CONTRACTS_DIR", REPO_ROOT / "contracts"))
