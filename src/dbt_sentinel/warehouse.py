"""Warehouse adapters — the only place that knows how to run SQL somewhere.

dbt-sentinel needs exactly one thing from a warehouse: run a query and hand back
column names, types, and rows. Everything else (parsing artifacts, prompting,
reporting) is engine-agnostic. Keeping that behind a small interface is what lets
DuckDB and BigQuery coexist without the rest of the codebase caring which is in
use.

Adapters are read-only by contract: dbt-sentinel inspects data, it never mutates
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class QueryResult:
    """Columns as (name, type) pairs, plus the rows themselves."""

    columns: list[tuple[str, str]]
    rows: list[dict[str, Any]]


class Warehouse(Protocol):
    """Minimal interface every backend must satisfy."""

    name: str

    def query(self, sql: str) -> QueryResult:
        """Run read-only SQL and return columns + rows."""
        ...

    def close(self) -> None: ...


class DuckDBWarehouse:
    """A local DuckDB database file, opened read-only."""

    name = "duckdb"

    def __init__(self, path: str | Path) -> None:
        import duckdb

        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"DuckDB database not found at {p}.")
        self._con = duckdb.connect(str(p), read_only=True)

    def query(self, sql: str) -> QueryResult:
        cursor = self._con.execute(sql)
        columns = [(d[0], str(d[1])) for d in cursor.description]
        names = [c[0] for c in columns]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        return QueryResult(columns=columns, rows=rows)

    def close(self) -> None:
        self._con.close()


class BigQueryWarehouse:
    """Google BigQuery. Works against the free sandbox — no billing required.

    Auth comes from Application Default Credentials (`gcloud auth
    application-default login`) or GOOGLE_APPLICATION_CREDENTIALS, so no secrets
    are handled here.
    """

    name = "bigquery"

    def __init__(self, project: str, location: str | None = None) -> None:
        try:
            from google.cloud import bigquery
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "BigQuery support needs the 'bq' extra: uv sync --group bq"
            ) from exc

        self._client = bigquery.Client(project=project, location=location)

    def query(self, sql: str) -> QueryResult:
        job = self._client.query(sql)
        result = job.result()
        columns = [(field.name, field.field_type) for field in result.schema]
        rows = [dict(row.items()) for row in result]
        return QueryResult(columns=columns, rows=rows)

    def close(self) -> None:
        self._client.close()


def open_warehouse(
    duckdb_path: str | Path | None = None,
    bq_project: str | None = None,
    bq_location: str | None = None,
) -> Warehouse:
    """Pick a backend from whichever option was supplied.

    Exactly one of duckdb_path / bq_project must be given.
    """
    if duckdb_path and bq_project:
        raise ValueError("Specify either a DuckDB path or a BigQuery project, not both.")
    if duckdb_path:
        return DuckDBWarehouse(duckdb_path)
    if bq_project:
        return BigQueryWarehouse(bq_project, bq_location)
    raise ValueError("No warehouse specified: pass --db or --bq-project.")