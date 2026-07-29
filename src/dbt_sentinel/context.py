"""Gather grounding evidence for a failing dbt test.

For each :class:`~dbt_sentinel.parse.FailingTest`, this connects to the DuckDB
warehouse and pulls a small, capped sample of the rows that actually caused the
failure, plus their column types. That sample -- not the model name alone -- is
what lets the LLM layer explain *why* a test failed instead of guessing.

Why we run the test's compiled SQL rather than ``SELECT * FROM <model>``:
some models (like the taxi repo's ``int_trips_enriched``) are materialized
*ephemeral*, so they don't exist as tables in the warehouse -- dbt inlines them
as CTEs. The test's compiled SQL already contains that inlined logic and, when
run, returns exactly the offending rows. So it works regardless of how the
guarded model is materialized.

dbt sometimes stores a test's ``compiled_code`` wrapped in a count aggregate
(``select count(*) as failures ... from ( <rows> ) dbt_internal_test``). We
detect and unwrap that so we sample individual rows, not a single count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .parse import FailingTest

# Matches dbt's execution wrapper so we can recover the inner failing-rows query.
_WRAPPER_RE = re.compile(
    r"from\s*\(\s*(?P<inner>.*)\)\s*dbt_internal_test",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_SAMPLE_LIMIT = 20


@dataclass
class FailureContext:
    """A failing test plus the concrete evidence gathered from the warehouse."""

    test: FailingTest
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, type)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    sampled_count: int = 0
    note: str = ""  # human-readable account of how the sample was obtained


def connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open the DuckDB warehouse read-only (never mutate the user's data)."""
    p = Path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"DuckDB database not found at {p}.")
    return duckdb.connect(str(p), read_only=True)


def _failing_rows_sql(compiled_sql: str) -> str:
    """Return SQL that yields the offending rows, unwrapping the count form."""
    match = _WRAPPER_RE.search(compiled_sql)
    return match.group("inner").strip() if match else compiled_sql.strip()


def _jsonable(value: Any) -> Any:
    """Coerce warehouse types the JSON prompt can't hold into strings."""
    if isinstance(value, (Decimal, datetime, date)):
        return str(value)
    return value


def gather_context(
    test: FailingTest,
    con: duckdb.DuckDBPyConnection,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> FailureContext:
    """Pull a capped sample of offending rows for one failing test.

    Args:
        test: the failing test to gather evidence for.
        con: an open (read-only) DuckDB connection.
        sample_limit: max rows to return -- keep small so prompts stay cheap and
            no more data than necessary leaves the warehouse.
    """
    if not test.compiled_sql:
        return FailureContext(
            test=test,
            note="No compiled SQL in the manifest for this test; cannot sample rows.",
        )

    rows_sql = _failing_rows_sql(test.compiled_sql)
    sample_sql = f"select * from (\n{rows_sql}\n) as _sentinel_sample limit {sample_limit}"

    try:
        cursor = con.execute(sample_sql)
    except duckdb.Error as exc:
        return FailureContext(
            test=test,
            note=f"Could not run the test's compiled SQL to sample rows: {exc}",
        )

    columns = [(desc[0], str(desc[1])) for desc in cursor.description]
    col_names = [c[0] for c in columns]
    raw_rows = cursor.fetchall()
    sample_rows = [
        {name: _jsonable(val) for name, val in zip(col_names, row)} for row in raw_rows
    ]

    total = test.failure_count if test.failure_count is not None else "unknown"
    note = (
        f"Sampled {len(sample_rows)} of {total} offending row(s) by running the "
        f"test's compiled SQL against the warehouse."
    )
    return FailureContext(
        test=test,
        columns=columns,
        sample_rows=sample_rows,
        sampled_count=len(sample_rows),
        note=note,
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    from .parse import parse

    target_arg = sys.argv[1] if len(sys.argv) > 1 else "target"
    db_arg = sys.argv[2] if len(sys.argv) > 2 else "data/capstone.duckdb"

    connection = connect(db_arg)
    for failing in parse(target_arg):
        ctx = gather_context(failing, connection)
        print(f"=== {ctx.test.test_name} ===")
        print(ctx.note)
        if ctx.columns:
            print("columns:", ", ".join(f"{n} ({t})" for n, t in ctx.columns))
        for r in ctx.sample_rows[:3]:
            print(" ", r)
        print()