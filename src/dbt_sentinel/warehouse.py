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


class SnowflakeWarehouse:
    """Snowflake, authenticated with a key pair.

    Snowflake is deprecating single-factor password sign-ins (phased May-October
    2026): human users need MFA and service users must use key-pair, OAuth, PAT or
    WIF. Key-pair is the right default for a non-interactive tool -- it needs no
    human present and no password in the environment.

    Configure with SNOWFLAKE_* environment variables; see the README.
    """

    name = "snowflake"

    def __init__(
        self,
        account: str,
        user: str,
        private_key_path: str | Path,
        private_key_passphrase: str | None = None,
        warehouse: str | None = None,
        database: str | None = None,
        schema: str | None = None,
        role: str | None = None,
    ) -> None:
        try:
            import snowflake.connector
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "Snowflake support needs the 'sf' extra: uv sync --group sf "
                "(or pip install 'dbt-sentinel[sf]')"
            ) from exc

        key_path = Path(private_key_path).expanduser()
        if not key_path.is_file():
            raise FileNotFoundError(f"Private key not found at {key_path}.")

        passphrase = (
            private_key_passphrase.encode() if private_key_passphrase else None
        )
        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=passphrase
        ).private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        self._con = snowflake.connector.connect(
            account=account,
            user=user,
            private_key=private_key,
            warehouse=warehouse,
            database=database,
            schema=schema,
            role=role,
            client_session_keep_alive=False,
        )

    def query(self, sql: str) -> QueryResult:
        # cursor.description reports numeric type codes; translate them to names so
        # the grounding prompt sees "TEXT"/"FIXED" rather than "2"/"0".
        from snowflake.connector.constants import FIELD_ID_TO_NAME

        cur = self._con.cursor()
        try:
            cur.execute(sql)
            columns = [
                (d[0], FIELD_ID_TO_NAME.get(d[1], str(d[1]))) for d in cur.description
            ]
            names = [c[0] for c in columns]
            rows = [dict(zip(names, row)) for row in cur.fetchall()]
            return QueryResult(columns=columns, rows=rows)
        finally:
            cur.close()

    def close(self) -> None:
        self._con.close()


def open_warehouse(
    duckdb_path: str | Path | None = None,
    bq_project: str | None = None,
    bq_location: str | None = None,
    snowflake: dict[str, Any] | None = None,
) -> Warehouse:
    """Pick a backend from whichever option was supplied.

    Exactly one backend may be given. Snowflake takes a dict of connection
    parameters (account, user, private_key_path, ...) rather than a long list of
    keyword arguments, so adding engines does not keep widening this signature.
    """
    chosen = [
        name
        for name, value in (
            ("duckdb", duckdb_path),
            ("bigquery", bq_project),
            ("snowflake", snowflake),
        )
        if value
    ]
    if len(chosen) > 1:
        raise ValueError(f"Specify one warehouse, not several: {', '.join(chosen)}.")

    if duckdb_path:
        return DuckDBWarehouse(duckdb_path)
    if bq_project:
        return BigQueryWarehouse(bq_project, bq_location)
    if snowflake:
        return SnowflakeWarehouse(**snowflake)
    raise ValueError(
        "No warehouse specified: pass --db, --bq-project, or Snowflake settings."
    )