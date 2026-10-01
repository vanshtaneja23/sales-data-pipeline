"""Cross-source parity checks.

Compares the three independently published sources against each other:
  P1  store reference (store.csv)   vs  state mapping (store_states.csv)  -- key coverage
  P2  sales (train.csv)             vs  store reference                   -- orphan / silent stores
  P3  sales calendar coverage       vs  store reference x date spine      -- missing store-days

Every discrepancy is written to ops.parity_report and a JSON report file. Discrepancies covered
by an allowlist rule in contracts/parity.yml are status "known"; anything else is "fail" and
raises ParityError, failing the DAG.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import psycopg
import yaml

from sales_pipeline.config import contracts_dir
from sales_pipeline.ops import ensure_ops_tables

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gap:
    store_id: int
    gap_start: date
    gap_end: date
    days: int


@dataclass
class ParityResult:
    check: str
    left_source: str
    right_source: str
    status: Literal["pass", "known", "fail"]
    discrepancies: int
    sample: list[Any] = field(default_factory=list)
    detail: str = ""


class ParityError(RuntimeError):
    pass


# --- SQL probes ------------------------------------------------------------------------------

KEY_DIFF_SQL = """
SELECT 'only_in_left' AS side, store_id
FROM (SELECT store_id FROM {left} EXCEPT SELECT store_id FROM {right}) a
UNION ALL
SELECT 'only_in_right', store_id FROM (SELECT store_id FROM {right} EXCEPT SELECT store_id FROM {left}) b
ORDER BY 2
"""

# Gaps-and-islands: every (reference store x calendar day) with no sales row, collapsed into
# contiguous ranges. The calendar spans the sales file's own min..max date.
MISSING_STORE_DAYS_SQL = """
WITH spine AS (
  SELECT generate_series(min(sales_date), max(sales_date), interval '1 day')::date AS d FROM raw.sales
),
missing AS (
  SELECT st.store_id, spine.d
  FROM raw.stores st CROSS JOIN spine
  LEFT JOIN raw.sales s ON s.store_id = st.store_id AND s.sales_date = spine.d
  WHERE s.store_id IS NULL
),
islands AS (
  SELECT store_id, d, d - (row_number() OVER (PARTITION BY store_id ORDER BY d))::int AS grp FROM missing
)
SELECT store_id, min(d) AS gap_start, max(d) AS gap_end, count(*)::int AS days
FROM islands GROUP BY store_id, grp ORDER BY store_id, gap_start
"""


def _key_diff(conn: psycopg.Connection, left: str, right: str) -> tuple[list[int], list[int]]:
    rows = conn.execute(KEY_DIFF_SQL.format(left=left, right=right)).fetchall()  # noqa: S608 (fixed table names)
    return [r[1] for r in rows if r[0] == "only_in_left"], [r[1] for r in rows if r[0] == "only_in_right"]


def find_missing_store_days(conn: psycopg.Connection) -> list[Gap]:
    return [Gap(*r) for r in conn.execute(MISSING_STORE_DAYS_SQL).fetchall()]


# --- pure logic (unit-tested) -----------------------------------------------------------------

def load_rules(path: Path | None = None) -> list[dict]:
    raw = yaml.safe_load((path or contracts_dir() / "parity.yml").read_text())
    return raw.get("known_missing_store_days", [])


def _rule_matches(rule: dict, gap: Gap) -> bool:
    stores = rule.get("stores")
    return (
        (stores is None or gap.store_id in stores)
        and gap.gap_start >= rule["date_from"]
        and gap.gap_end <= rule["date_to"]
    )


def classify_gaps(gaps: list[Gap], rules: list[dict]) -> ParityResult:
    """Split gaps into allowlisted vs unexplained; also fail if a known gap affects more stores."""
    explained: dict[str, set[int]] = {r["id"]: set() for r in rules}
    unexplained: list[Gap] = []
    for gap in gaps:
        rule = next((r for r in rules if _rule_matches(r, gap)), None)
        if rule:
            explained[rule["id"]].add(gap.store_id)
        else:
            unexplained.append(gap)

    grown = {rid: len(s) for rid, s in explained.items()
             if len(s) > next(r for r in rules if r["id"] == rid).get("max_stores", float("inf"))}
    missing_days = sum(g.days for g in gaps)
    summary = {rid: len(s) for rid, s in explained.items()}

    if unexplained or grown:
        detail = []
        if unexplained:
            detail.append(f"{len(unexplained)} gap(s) not covered by any rule in parity.yml")
        if grown:
            detail.append(f"known gaps now affect more stores than baselined: {grown}")
        return ParityResult(
            "sales_calendar_coverage", "sales", "stores x calendar", "fail", missing_days,
            sample=[asdict(g) for g in unexplained[:10]], detail="; ".join(detail),
        )
    return ParityResult(
        "sales_calendar_coverage", "sales", "stores x calendar", "known" if gaps else "pass", missing_days,
        sample=[{"rule": rid, "stores": n} for rid, n in summary.items() if n],
        detail=f"{len(gaps)} gap range(s), all explained by known-issue rules" if gaps else "",
    )


def key_coverage_result(
    check: str, left: str, right: str, only_left: list[int], only_right: list[int]
) -> ParityResult:
    n = len(only_left) + len(only_right)
    return ParityResult(
        check, left, right, "fail" if n else "pass", n,
        sample=[{"only_in_" + left: only_left[:10]}, {"only_in_" + right: only_right[:10]}] if n else [],
        detail=f"{len(only_left)} store(s) only in {left}, {len(only_right)} only in {right}" if n else "",
    )


# --- task entry point ---------------------------------------------------------------------------

def run_parity(conn: psycopg.Connection, batch_id: str, report_dir: Path) -> list[dict]:
    results = [
        key_coverage_result("store_reference_vs_state_mapping", "stores", "store_states",
                            *_key_diff(conn, "raw.stores", "raw.store_states")),
        key_coverage_result("sales_vs_store_reference", "sales", "stores",
                            *_key_diff(conn, "(SELECT DISTINCT store_id FROM raw.sales) s", "raw.stores")),
        classify_gaps(find_missing_store_days(conn), load_rules()),
    ]

    ensure_ops_tables(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ops.parity_report (batch_id, check_name, left_source, right_source, status, "
            "discrepancies, sample, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(batch_id, r.check, r.left_source, r.right_source, r.status, r.discrepancies,
              json.dumps(r.sample, default=str), r.detail) for r in results],
        )
    if not conn.autocommit:
        conn.commit()  # persist the report before ParityError can trigger a rollback

    report_dir.mkdir(parents=True, exist_ok=True)
    safe_batch = "".join(c if c.isalnum() or c in "-_" else "_" for c in batch_id)
    report = report_dir / f"parity_{safe_batch}.json"
    report.write_text(json.dumps([asdict(r) for r in results], indent=2, default=str))

    for r in results:
        log.log(logging.ERROR if r.status == "fail" else logging.INFO,
                "parity %s", json.dumps(asdict(r), default=str))
    failures = [r for r in results if r.status == "fail"]
    if failures:
        raise ParityError(
            f"{len(failures)} parity check(s) failed (report: {report}):\n"
            + "\n".join(f"  - {f.check}: {f.discrepancies} discrepancies. {f.detail}" for f in failures)
        )
    return [asdict(r) for r in results]
