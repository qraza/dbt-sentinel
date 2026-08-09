"""Tests for the warehouse adapter layer.

DuckDB is exercised for real against a temporary database (cheap, no network).
BigQuery is never contacted: we only assert the selection logic and that a fake
adapter satisfying the Warehouse protocol flows correctly through
gather_context, which is what actually matters for engine-independence.
"""

from __future__ import annotations

import duckdb
import pytest

from dbt_sentinel.context import gather_context
from dbt_sentinel.parse import FailingTest
from dbt_sentinel.warehouse import (
    DuckDBWarehouse,
    QueryResult,
    open_warehouse,
)


@pytest.fixture
def duck_db(tmp_path):
    """A tiny DuckDB file with one table of implausible speeds."""
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "create table trips as select * from (values (0.81, 5, 97.2), (1.05, 5, 126.0)) "
        "as t(trip_distance, trip_duration_minutes, avg_speed_mph)"
    )
    con.close()
    return path


# --- open_warehouse selection -------------------------------------------


def test_open_warehouse_returns_duckdb(duck_db) -> None:
    wh = open_warehouse(duckdb_path=duck_db)
    assert wh.name == "duckdb"
    wh.close()


def test_open_warehouse_requires_one_backend() -> None:
    with pytest.raises(ValueError, match="No warehouse specified"):
        open_warehouse()


def test_open_warehouse_rejects_both_backends(duck_db) -> None:
    with pytest.raises(ValueError, match="not both"):
        open_warehouse(duckdb_path=duck_db, bq_project="some-project")


def test_duckdb_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        DuckDBWarehouse(tmp_path / "nope.duckdb")


# --- DuckDB adapter behaviour -------------------------------------------


def test_duckdb_query_returns_columns_and_rows(duck_db) -> None:
    wh = DuckDBWarehouse(duck_db)
    result = wh.query("select * from trips order by avg_speed_mph")
    assert [c[0] for c in result.columns] == [
        "trip_distance",
        "trip_duration_minutes",
        "avg_speed_mph",
    ]
    assert len(result.rows) == 2
    assert float(result.rows[0]["avg_speed_mph"]) == 97.2
    wh.close()


def test_duckdb_is_read_only(duck_db) -> None:
    wh = DuckDBWarehouse(duck_db)
    with pytest.raises(Exception):  # noqa: B017 - engine raises its own error type
        wh.query("create table should_fail as select 1 as x")
    wh.close()


# --- engine independence -------------------------------------------------


class FakeWarehouse:
    """Stands in for any non-DuckDB engine (e.g. BigQuery) without network."""

    name = "fake-engine"

    def __init__(self) -> None:
        self.last_sql: str | None = None

    def query(self, sql: str) -> QueryResult:
        self.last_sql = sql
        return QueryResult(
            columns=[("avg_speed_mph", "FLOAT")],
            rows=[{"avg_speed_mph": 97.2}],
        )

    def close(self) -> None:
        pass


def test_gather_context_works_with_any_warehouse() -> None:
    """context.py must not care which engine it is given."""
    test = FailingTest(
        unique_id="test.p.speed",
        test_name="speed",
        status="fail",
        failure_count=2,
        message=None,
        test_type=None,
        compiled_sql="select * from trips where avg_speed_mph > 80",
    )
    wh = FakeWarehouse()
    ctx = gather_context(test, wh, sample_limit=5)

    assert ctx.sample_rows == [{"avg_speed_mph": 97.2}]
    assert ctx.columns == [("avg_speed_mph", "FLOAT")]
    # The engine name appears in the note, and the limit was applied.
    assert "fake-engine" in ctx.note
    assert "limit 5" in wh.last_sql


def test_gather_context_reports_engine_errors_gracefully() -> None:
    class BrokenWarehouse(FakeWarehouse):
        def query(self, sql: str) -> QueryResult:
            raise RuntimeError("permission denied")

    test = FailingTest(
        unique_id="test.p.speed",
        test_name="speed",
        status="fail",
        failure_count=1,
        message=None,
        test_type=None,
        compiled_sql="select 1",
    )
    ctx = gather_context(test, BrokenWarehouse())
    assert ctx.sample_rows == []
    assert "permission denied" in ctx.note