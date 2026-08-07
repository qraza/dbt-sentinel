"""Persist each dbt-sentinel run so failures can be tracked over time.

A single failing test tells you something is wrong *now*. Knowing whether it is
new, has been failing for weeks, or just came back after a fix is what turns a
red light into a trend -- so this module appends every run to a small DuckDB
database and classifies each failure against the previous run.

The history database is separate from the warehouse being analyzed: it is
dbt-sentinel's own state, written to `.sentinel/history.duckdb` by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .analyze import Analysis
from .parse import FailingTest

DEFAULT_HISTORY_PATH = Path(".sentinel/history.duckdb")

# How a failure relates to the previous run of the same test.
NEW = "new"  # not failing last time (or never seen before)
RECURRING = "recurring"  # failed last time too
REGRESSED = "regressed"  # passed last time, failing now (seen before)

_SCHEMA = """
create table if not exists runs (
    run_id      bigint,
    run_at      timestamp,
    project     varchar
);
create table if not exists test_results (
    run_id         bigint,
    unique_id      varchar,
    test_name      varchar,
    status         varchar,
    failure_count  bigint,
    confidence     varchar,
    root_cause     varchar
);
"""


@dataclass
class HistoryEntry:
    """One test's outcome in one past run."""

    run_at: datetime
    status: str
    failure_count: int | None
    confidence: str | None


def connect(path: str | Path = DEFAULT_HISTORY_PATH) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the history database and ensure the schema."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(_SCHEMA)
    return con


def _next_run_id(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("select coalesce(max(run_id), 0) + 1 from runs").fetchone()
    return int(row[0]) if row else 1


def previous_statuses(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Return {unique_id: status} from the most recent run, if any."""
    row = con.execute("select max(run_id) from runs").fetchone()
    if not row or row[0] is None:
        return {}
    last_run = row[0]
    rows = con.execute(
        "select unique_id, status from test_results where run_id = ?", [last_run]
    ).fetchall()
    return {unique_id: status for unique_id, status in rows}


def classify(unique_id: str, previous: dict[str, str], seen_ever: bool) -> str:
    """Label a currently-failing test relative to the previous run.

    Args:
        unique_id: the test's dbt unique_id.
        previous: {unique_id: status} from the last run.
        seen_ever: whether this test appears anywhere in history.
    """
    prior = previous.get(unique_id)
    if prior is None:
        # Not in the last run at all: brand new, or previously passing and
        # therefore never recorded as a failure.
        return REGRESSED if seen_ever else NEW
    return RECURRING


def has_been_seen(con: duckdb.DuckDBPyConnection, unique_id: str) -> bool:
    """True if this test has ever been recorded as failing before."""
    row = con.execute(
        "select count(*) from test_results where unique_id = ?", [unique_id]
    ).fetchone()
    return bool(row and row[0])


def record_run(
    con: duckdb.DuckDBPyConnection,
    results: list[tuple[FailingTest, Analysis]],
    project: str = "",
) -> int:
    """Append this run's failures to history. Returns the new run_id."""
    run_id = _next_run_id(con)
    con.execute(
        "insert into runs values (?, ?, ?)",
        [run_id, datetime.now(UTC), project],
    )
    for test, analysis in results:
        con.execute(
            "insert into test_results values (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                test.unique_id,
                test.test_name,
                test.status,
                test.failure_count,
                analysis.confidence if analysis else None,
                (analysis.root_cause[:2000] if analysis else None),
            ],
        )
    return run_id


def history(con: duckdb.DuckDBPyConnection, unique_id: str) -> list[HistoryEntry]:
    """Every recorded outcome for one test, oldest first."""
    rows = con.execute(
        """
        select r.run_at, t.status, t.failure_count, t.confidence
        from test_results t
        join runs r using (run_id)
        where t.unique_id = ?
        order by r.run_at
        """,
        [unique_id],
    ).fetchall()
    return [
        HistoryEntry(run_at=run_at, status=status, failure_count=fc, confidence=conf)
        for run_at, status, fc, conf in rows
    ]