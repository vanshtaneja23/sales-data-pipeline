from __future__ import annotations

import logging
from types import SimpleNamespace

from sales_pipeline.alerts import build_alert, on_task_failure


def _context(exc: Exception | None = None) -> dict:
    ti = SimpleNamespace(dag_id="sales_pipeline", task_id="validate_sales")
    return {"ti": ti, "run_id": "manual__x", "exception": exc}


def test_alert_captures_task_and_error():
    alert = build_alert(_context(ValueError("bad rows")))
    assert alert == {
        "event": "pipeline_alert", "dag_id": "sales_pipeline", "task_id": "validate_sales",
        "run_id": "manual__x", "error": "ValueError: bad rows",
    }


def test_alert_error_is_truncated():
    assert len(build_alert(_context(ValueError("x" * 10_000)))["error"]) == 4000


def test_callback_logs_even_when_sink_unavailable(monkeypatch, caplog):
    # No warehouse env: persisting fails, but the alert is still logged and nothing raises.
    monkeypatch.delenv("WAREHOUSE_PASSWORD", raising=False)
    with caplog.at_level(logging.ERROR, logger="sales_pipeline.alerts"):
        on_task_failure(_context(RuntimeError("dq failed")))
    messages = [r.getMessage() for r in caplog.records]
    assert any("PIPELINE_ALERT" in m and "dq failed" in m for m in messages)
    assert any("Could not persist" in m for m in messages)
